import asyncio
import base64
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from deeptutor.runtime.backup import backup, restore, runtime_lock, verify
from deeptutor.services.llm.metrics import measure_provider_call
from deeptutor.services.llm.task_budget import (
    BudgetLimits,
    TaskBudget,
    TaskBudgetExceeded,
    current_budget,
)


def test_backup_preserves_wal_documents_and_resume_identity(tmp_path):
    home = tmp_path / "runtime"
    (home / "data/materials/revision-1").mkdir(parents=True)
    (home / "data/materials/revision-1/source.txt").write_text("第七题的完整资料")
    connection = sqlite3.connect(home / "data/session.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE attempts(id TEXT PRIMARY KEY, result TEXT)")
    connection.execute("INSERT INTO attempts VALUES ('original-attempt', 'correct')")
    connection.commit()
    snapshot = tmp_path / "backup"
    backup(home, snapshot)
    verify(snapshot)
    restored = tmp_path / "restored"
    restore(snapshot, restored)
    with sqlite3.connect(restored / "data/session.db") as db:
        assert db.execute("SELECT * FROM attempts").fetchone() == ("original-attempt", "correct")
    assert (restored / "data/materials/revision-1/source.txt").read_text() == "第七题的完整资料"
    with pytest.raises(ValueError, match="never overwritten"):
        restore(snapshot, restored)
    connection.close()
    with runtime_lock(home), pytest.raises(RuntimeError, match="in use"):
        backup(home, tmp_path / "blocked")
    (snapshot / "data/materials/revision-1/source.txt").write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        verify(snapshot)


def test_budget_reserves_concurrent_requests_and_never_resets_call_count():
    budget = TaskBudget(limits=BudgetLimits(calls=2, tokens=1000, seconds=30, concurrency=2))
    first, out1 = budget.admit(100, 500)
    second, out2 = budget.admit(100, 500)
    assert out1 == 500 and out2 == 300
    with pytest.raises(TaskBudgetExceeded):
        budget.admit(1, 128)
    budget.finish(first, 200)
    budget.finish(second, 200)
    assert budget.active == budget.reserved == 0
    with pytest.raises(TaskBudgetExceeded, match="model-call"):
        budget.admit(1, 128)


@pytest.mark.asyncio
async def test_transport_deadline_interrupts_provider_and_releases_budget():
    budget = TaskBudget(limits=BudgetLimits(seconds=0.01))

    async def stalled(**kwargs):
        await asyncio.sleep(5)

    token = current_budget.set(budget)
    try:
        with pytest.raises(TimeoutError):
            await measure_provider_call(stalled, messages=[], max_tokens=128)
        assert budget.active == budget.reserved == 0
    finally:
        current_budget.reset(token)


def test_local_codex_bridge_never_copies_or_mutates_refresh_token(tmp_path, monkeypatch):
    from deeptutor.services.codex_auth.contracts import CodexAuthError
    from deeptutor.services.codex_auth.local_login import (
        LocalCodexCredentialStore,
        local_login_path,
    )

    auth = tmp_path / "auth.json"

    def token(exp):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"header.{payload}.signature"

    auth.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": token(3000000000),
                    "refresh_token": "never-copy-this",
                    "account_id": "sample",
                }
            }
        )
    )
    original = auth.read_bytes()
    store = LocalCodexCredentialStore(tmp_path / "app", auth)
    credentials = store.load_credentials()
    assert credentials.refresh_token == "managed-by-local-codex"
    with pytest.raises(CodexAuthError, match="managed"):
        store.clear_credentials()
    assert auth.read_bytes() == original
    assert all("never-copy-this" not in p.read_text() for p in (tmp_path / "app").rglob("*.json"))
    monkeypatch.setenv("DEEPTUTOR_USE_CODEX_LOGIN", "1")
    monkeypatch.setattr(
        "deeptutor.multi_user.context.get_current_user",
        lambda: SimpleNamespace(is_admin=False, scope=SimpleNamespace(kind="user")),
    )
    assert local_login_path() is None


def test_new_research_modes_have_distinct_instructions():
    from deeptutor.agents.research.request_config import validate_research_request_config
    from deeptutor.agents.research.workflows import workflow_instruction

    for mode in ["comparison", "citation_audit", "experiment_plan"]:
        config = validate_research_request_config({"mode": mode, "depth": "standard"})
        assert config.mode == mode
        assert workflow_instruction(mode)
    assert workflow_instruction("citation_audit") != workflow_instruction("experiment_plan")


def test_exact_locators_do_not_match_different_numbers_or_cross_page_neighbors():
    from llama_index.core.schema import NodeWithScore, TextNode

    from deeptutor.services.rag.pipelines.llamaindex.study_locator import (
        locator_pattern,
        prioritize_located_nodes,
    )

    assert locator_pattern("解释第7题").search("第7题 已知条件")
    assert not locator_pattern("解释第7题").search("第17题")
    assert locator_pattern("equation 2.1").search("(2.1) x = y")
    nodes = [
        TextNode(text="第7题", id_="q", metadata={"document_key": "a", "page_index": 2}),
        TextNode(
            text="图与完整条件", id_="neighbor", metadata={"document_key": "a", "page_index": 2}
        ),
        TextNode(text="另一份资料", id_="other", metadata={"document_key": "b", "page_index": 2}),
    ]
    index = SimpleNamespace(docstore=SimpleNamespace(docs={n.node_id: n for n in nodes}))
    found = prioritize_located_nodes(index, "第7题", [], top_k=4)
    assert [n.node.node_id for n in found] == ["q", "neighbor"]


def test_chinese_bm25_retrieves_without_spaces():
    from llama_index.core.schema import TextNode
    from llama_index.retrievers.bm25 import BM25Retriever

    from deeptutor.services.rag.pipelines.llamaindex.retrievers import BM25_OPTIONS

    nodes = [
        TextNode(text="牛顿第二定律联系力与加速度", id_="physics"),
        TextNode(text="细胞与生物学", id_="biology"),
    ]
    retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=1, **BM25_OPTIONS)
    assert retriever.retrieve("加速度")[0].node.node_id == "physics"


def test_confidence_alone_never_rewrites_the_explicit_verdict():
    from deeptutor.learning.semantic_assessment import Verdict

    assert Verdict(result="correct", confidence=0.2, feedback="Equivalent").receipt()["final"]


@pytest.mark.asyncio
async def test_mastery_outbox_survives_failure_and_replays_the_same_attempt(tmp_path, monkeypatch):
    from deeptutor.learning.assessment import AssessmentRecord
    from deeptutor.learning.mastery_projection import (
        projection_records,
        reconcile_mastery_projections,
    )
    from deeptutor.learning.models import (
        InteractionStatus,
        LearningEvidence,
        LearningProgress,
        MasteryInteraction,
        PendingQuestion,
    )
    from deeptutor.learning.storage import LearningStore

    store = LearningStore(root=tmp_path)
    progress = LearningProgress(
        book_id="path",
        name="A course",
        learning_evidence=[
            LearningEvidence(
                question_id="q", knowledge_point_id="kp", attempt_count=2, result="correct"
            )
        ],
    )
    store.save(progress)
    question = PendingQuestion(
        question_id="q",
        knowledge_point_id="kp",
        prompt="2+2?",
        expected_answer="4",
        question_type="open",
    )
    interaction = MasteryInteraction(
        interaction_id="i",
        path_id="path",
        question=question,
        session_id="s",
        turn_id="t",
        user_answer="4",
        status=InteractionStatus.GRADED,
        result={"result": "correct", "is_correct": True, "final": True},
    )
    store.mutate("path", lambda tx: tx.put_interaction(interaction))
    row = store.pending_projections()[0]
    record = projection_records(json.loads(row["payload_json"]))[0]
    expected = AssessmentRecord(
        question="2+2?",
        session_id="s",
        turn_id="t",
        question_id="q",
        source="mastery_path",
        assessment_type="quiz",
        attempt_count=2,
        user_answer="4",
        result="correct",
        is_correct=True,
    )
    assert record.attempt_identity == expected.attempt_identity

    async def fail(_):
        raise RuntimeError("projection offline")

    monkeypatch.setattr("deeptutor.learning.mastery_projection.record_assessment", fail)
    assert await reconcile_mastery_projections(store) == (0, 1)
    recovered = []

    async def succeed(record):
        recovered.append(record.attempt_identity)

    monkeypatch.setattr("deeptutor.learning.mastery_projection.record_assessment", succeed)
    assert await reconcile_mastery_projections(LearningStore(root=tmp_path)) == (1, 0)
    assert await reconcile_mastery_projections(store) == (0, 0)
    assert recovered == [expected.attempt_identity]


def test_token_count_never_downloads_a_tokenizer_on_cold_start(monkeypatch):
    import tiktoken

    from deeptutor.services.session import local_tokenizer
    from deeptutor.services.session.context_builder import count_tokens

    monkeypatch.setattr(local_tokenizer, "context_encoding", lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Token estimation must not download vocabulary")

    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    assert count_tokens("树与图") == len("树与图".encode("utf-8"))
    assert count_tokens("") == 0


def test_codex_report_uses_supported_minimum_effort(monkeypatch):
    from types import SimpleNamespace

    from deeptutor.services import config
    from deeptutor.services.llm.reasoning_params import thinking_off_effort_for

    catalog = {
        "services": {
            "llm": {
                "profiles": [
                    {
                        "models": [
                            {
                                "model": "reasoning-only",
                                "codex_supported_reasoning_levels": ["low", "medium", "high"],
                            },
                            {
                                "model": "can-disable",
                                "codex_supported_reasoning_levels": ["none", "low"],
                            },
                        ]
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(
        config, "get_model_catalog_service", lambda: SimpleNamespace(load=lambda: catalog)
    )
    assert thinking_off_effort_for("openai_codex", "reasoning-only") == "low"
    assert thinking_off_effort_for("openai_codex", "can-disable") == "none"


def test_bundled_tokenizer_works_without_network(monkeypatch):
    import tiktoken

    from deeptutor.services.session.context_builder import count_tokens
    from deeptutor.services.session.local_tokenizer import context_encoding

    def forbidden(*args, **kwargs):
        raise AssertionError("No vocabulary download permitted")

    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    context_encoding.cache_clear()
    assert context_encoding() is not None
    assert count_tokens("Hello, world!") == 4


def test_restore_rebases_local_sources_without_rewriting_prose(tmp_path):
    import json

    home = tmp_path / "original"
    data = home / "data"
    data.mkdir(parents=True)
    source = data / "knowledge_bases/k/raw/paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"PDF fixture")
    (data / "index.json").write_text(
        json.dumps(
            {
                "file_path": str(source),
                "text": str(source),
                "nested": {"version_path": str(source.parent)},
            }
        )
    )
    (data / "corpus.jsonl").write_text(
        json.dumps(
            {
                "metadata": {"file_path": str(source)},
                "_node_content": json.dumps(
                    {"metadata": {"file_path": str(source)}, "text": str(source)}
                ),
            }
        )
        + "\n"
    )
    snapshot, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(home, snapshot)
    restore(snapshot, restored)
    result = json.loads((restored / "data/index.json").read_text())
    assert result["file_path"] == str(restored / "data/knowledge_bases/k/raw/paper.pdf")
    assert result["text"] == str(source)
    assert (
        json.loads((restored / "data/corpus.jsonl").read_text())["metadata"]["file_path"]
        == result["file_path"]
    )
    nested = json.loads(json.loads((restored / "data/corpus.jsonl").read_text())["_node_content"])
    assert nested["metadata"]["file_path"] == result["file_path"]
    assert nested["text"] == str(source)
    verify(snapshot)


def test_module_catalog_ignores_macos_sidecars(monkeypatch, tmp_path):
    import shutil

    from deeptutor.modules import catalog

    original = Path(catalog.__file__).parent / "manifests"
    shutil.copytree(original, tmp_path / "manifests")
    (tmp_path / "manifests/._reading.json").write_bytes(b"\x00\xb0macOS resource fork")
    catalog.manifests.cache_clear()
    monkeypatch.setattr(catalog, "__file__", str(tmp_path / "catalog.py"))
    try:
        assert "reading" in catalog.manifests()
    finally:
        catalog.manifests.cache_clear()


def test_restore_rebases_project_catalog_without_copying_memory_edges(tmp_path):
    import json
    import sqlite3

    home = tmp_path / "source"
    project = home / "data/user/workspaces/ws_a"
    memory = project / ".deeptutor/data/memory/L3/preferences.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Private project preference")
    catalog = home / "data/user/.runtime/workspaces.sqlite3"
    catalog.parent.mkdir(parents=True)
    with sqlite3.connect(catalog) as db:
        db.execute("CREATE TABLE workspaces(id TEXT PRIMARY KEY,payload TEXT)")
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute(
            "INSERT INTO workspaces VALUES (?,?)",
            (
                "ws_a",
                json.dumps(
                    {
                        "path": str(project),
                        "instructions": str(project),
                        "memory_policy": {"sources": ["ws_b"]},
                    }
                ),
            ),
        )
        db.execute("INSERT INTO metadata VALUES (?,?)", ("root", json.dumps(str(project.parent))))
    snapshot, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(home, snapshot)
    restore(snapshot, restored)
    with sqlite3.connect(restored / "data/user/.runtime/workspaces.sqlite3") as db:
        row = json.loads(db.execute("SELECT payload FROM workspaces").fetchone()[0])
        assert row["path"] == str(restored / "data/user/workspaces/ws_a")
        assert row["instructions"] == str(project)
        assert row["memory_policy"] == {"sources": ["ws_b"]}
    assert (
        restored / "data/user/workspaces/ws_a/.deeptutor/data/memory/L3/preferences.md"
    ).read_text() == "Private project preference"
