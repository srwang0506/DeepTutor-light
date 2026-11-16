from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
import json
from pathlib import Path
import threading

import fitz
import pytest

from deeptutor.core.object_reference import ObjectRef
from deeptutor.services.session import get_sqlite_session_store
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context
from deeptutor.studio import catalog, paper_reviews, tutor_sources
from deeptutor.studio.database import Conflict, Missing, Studio
from deeptutor.studio.models import EditionInput, WorkInput


def paper(store):
    work = catalog.create_work(store, WorkInput(title="HOI fixture"))
    edition = catalog.create_edition(store, EditionInput(work_id=work["id"]))
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "The method uses a fixed HICO-DET split.")
        content = pdf.tobytes()
    return catalog.attach_pdf(store, edition["id"], content, "paper.pdf", edition["revision"])


def create(store, edition, key="paper-review-test-request", **extra):
    return paper_reviews.create(
        store,
        paper_reviews.PaperReviewCreate(
            request_id=key,
            edition_id=edition["id"],
            revision=edition["revision"],
            blob_hash=edition["data"]["files"][0]["hash"],
            **extra,
        ),
    )


@pytest.fixture
def context(as_user):
    with as_user("review-author", role="admin"):
        workspace = ContentWorkspaceService().create_workspace("HOI review")
        with workspace_context(workspace["workspace_id"]):
            store = Studio()
            edition = paper(store)
            yield store, edition


def staged(tmp_path, row, name="turn"):
    task = tmp_path.resolve() / name
    ref = ObjectRef.model_validate(row["review_ref"])
    sid = tutor_sources.source_id(ref)
    refs = {sid: row["review_ref"]}
    env = paper_reviews.prepare_execution(task, refs)
    file = Path(env["env"]["DEEPTUTOR_PAPER_REVIEW_DIR"]) / "review.json"
    data = json.loads(file.read_text())
    data["comments"] = [
        {
            "id": "W01",
            "title": "Specify the split",
            "body": "Make the evaluation split explicit.",
            "request": "Name the exact split in the experiment section.",
            "status": "confirmed",
            "severity": "minor",
            "confidence": "high",
            "scope": "page",
            "page": 1,
            "dimension": "experiments",
            "countercheck": "The description is brief.",
            "evidence": [
                {
                    "source_id": "paper",
                    "page": 1,
                    "quote": "The method uses a fixed HICO-DET split.",
                }
            ],
        }
    ]
    file.write_text(json.dumps(data))
    return task, refs, sid, file, data


def test_creation_replay_target_and_project_isolation(context):
    store, edition = context
    first = create(store, edition)
    assert create(store, edition)["id"] == first["id"]
    with pytest.raises(Conflict):
        create(store, edition, instructions="Different intent")
    assert first["data"]["target_ref"]["revision"] == edition["revision"]
    assert first["review_ref"]["revision"] == 1
    assert "2026-11-02" in first["data"]["skill"]["form_snapshot"]
    other = ContentWorkspaceService().create_workspace("Other")
    with workspace_context(other["workspace_id"]):
        assert paper_reviews.list_reviews(Studio())["reviews"] == []
        with pytest.raises(Missing):
            paper_reviews.get(Studio(), first["id"])
        _, refs, unavailable = tutor_sources.prepare_turn(
            [ObjectRef.model_validate(first["review_ref"])]
        )
        assert not refs and unavailable == 1


@pytest.mark.asyncio
async def test_bind_creates_one_real_owned_session_and_rejects_replacement(context):
    store, edition = context
    row = create(store, edition, language="en")
    first = await paper_reviews.bind_session(store, row["id"])
    second = await paper_reviews.bind_session(store, row["id"])
    assert first["data"]["session_id"] == second["data"]["session_id"]
    assert first["revision"] == second["revision"]
    saved = await get_sqlite_session_store().get_session(first["data"]["session_id"])
    assert saved["preferences"]["workspace_id"] == store.project
    assert saved["preferences"]["reply_language_override"] == "en"
    assert "English" in paper_reviews.source_context(store, row["id"])["output_language_rule"]
    await get_sqlite_session_store().update_session_preferences(
        first["data"]["session_id"], {"reply_language_override": "zh"}
    )
    await paper_reviews.bind_session(store, row["id"])
    saved = await get_sqlite_session_store().get_session(first["data"]["session_id"])
    assert saved["preferences"]["reply_language_override"] == "zh"
    with pytest.raises(Conflict):
        await paper_reviews.bind_session(store, row["id"], "unrelated-session")


def test_checkpoint_replay_resume_and_author_tracking(context, tmp_path):
    store, edition = context
    row = create(store, edition, language="en")
    task, refs, sid, file, _ = staged(tmp_path, row)
    assert json.loads(file.read_text())["language"] == "en"
    first = paper_reviews.checkpoint(task, refs, sid)
    assert first["data"]["issues"][0]["location_status"] == "matched"
    assert first["data"]["checks"]["scientific_claims"] == "not_automatically_verified"
    assert first["data"]["checks"]["upstream"] == "incomplete"
    assert paper_reviews.checkpoint(task, refs, sid)["revision"] == first["revision"]
    changed = paper_reviews.update_issue(
        store,
        row["id"],
        "W01",
        paper_reviews.PaperReviewIssueUpdate(
            expected_revision=first["revision"], status="working", response="Adding details."
        ),
    )
    with pytest.raises(Conflict):
        paper_reviews.update_issue(
            store,
            row["id"],
            "W01",
            paper_reviews.PaperReviewIssueUpdate(expected_revision=first["revision"]),
        )
    next_env = paper_reviews.prepare_execution(tmp_path.resolve() / "next", refs)
    resumed = json.loads(
        (Path(next_env["env"]["DEEPTUTOR_PAPER_REVIEW_DIR"]) / "review.json").read_text()
    )
    assert resumed["comments"][0]["id"] == "W01"
    assert paper_reviews.get(store, row["id"])["data"]["issues"][0]["tracking_status"] == "working"
    exported = json.loads(paper_reviews.export(store, row["id"])["content"])
    assert exported["review"]["revision"] == changed["revision"]
    assert exported["snapshot"]["paper"]["pdf_sha256"] == edition["data"]["files"][0]["hash"]
    source = tutor_sources.read(ObjectRef.model_validate(row["review_ref"]))
    assert "paper_review_checkpoint" in source["text"]


def test_quote_validation_ignores_forged_snapshot_text(context, tmp_path):
    store, edition = context
    row = create(store, edition)
    task, refs, sid, file, data = staged(tmp_path, row)
    quote = "This invented result never appeared in the paper."
    data["comments"][0]["evidence"][0]["quote"] = quote
    (file.parent / "sources/paper.txt").write_text(quote)
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="does not match"):
        paper_reviews.checkpoint(task, refs, sid)
    assert paper_reviews.get(store, row["id"])["data"]["checkpoint_hash"] == ""


@pytest.mark.parametrize("mutation", ["hash", "symlink", "escape", "sources", "evidence", "status"])
def test_checkpoint_rejects_untrusted_structure(context, tmp_path, mutation):
    store, edition = context
    row = create(store, edition)
    task, refs, sid, file, data = staged(tmp_path, row)
    if mutation == "hash":
        data["paper"]["pdf_sha256"] = "0" * 64
    elif mutation == "escape":
        data["sources"][0]["path"] = "../../outside.txt"
    elif mutation == "sources":
        data["sources"] = "invalid"
    elif mutation == "evidence":
        data["comments"][0]["evidence"] = {}
    elif mutation == "status":
        data["comments"][0]["status"] = "scientifically-proven"
    file.write_text(json.dumps(data))
    if mutation == "symlink":
        moved = file.parent / "other.json"
        file.rename(moved)
        file.symlink_to(moved)
    with pytest.raises(ValueError):
        paper_reviews.checkpoint(task, refs, sid)


def test_stale_parallel_checkpoint_cannot_overwrite_newer_review(context, tmp_path):
    store, edition = context
    row = create(store, edition)
    first = staged(tmp_path, row, "first")
    second = staged(tmp_path, row, "second")
    saved = paper_reviews.checkpoint(*first[:3])
    data = second[4]
    data["comments"][0]["title"] = "Different stale edit"
    second[3].write_text(json.dumps(data))
    with pytest.raises(Conflict, match="newer review"):
        paper_reviews.checkpoint(*second[:3])
    assert (
        paper_reviews.get(store, row["id"])["data"]["checkpoint_hash"]
        == saved["data"]["checkpoint_hash"]
    )


def test_side_artifacts_are_part_of_the_checkpoint_and_resume(context, tmp_path):
    store, edition = context
    row = create(store, edition)
    task, refs, sid, file, data = staged(tmp_path, row)
    data["policy"]["not_done"] = ["Table 2 visual comparison remains pending."]
    file.write_text(json.dumps(data))
    first = paper_reviews.checkpoint(task, refs, sid)
    notes = file.parent / "revision/issues.json"
    notes.parent.mkdir()
    notes.write_text(json.dumps({"issues": [{"id": "W01", "status": "working"}]}))
    second = paper_reviews.checkpoint(task, refs, sid)
    assert second["data"]["review_hash"] == first["data"]["review_hash"]
    assert second["data"]["checkpoint_hash"] != first["data"]["checkpoint_hash"]
    assert second["data"]["previous_checkpoint_hash"] == first["data"]["checkpoint_hash"]
    assert "Table 2 visual comparison remains pending." in second["data"]["pending"]
    assert paper_reviews.checkpoint(task, refs, sid)["revision"] == second["revision"]
    prepared = paper_reviews.prepare_execution(tmp_path.resolve() / "resumed", refs)
    restored = Path(prepared["env"]["DEEPTUTOR_PAPER_REVIEW_DIR"]) / "revision/issues.json"
    assert restored.read_bytes() == notes.read_bytes()
    # Early isolated acceptance records used the review JSON itself as checkpoint.
    with store.db(write=True) as db:
        legacy = store.put(
            db,
            "paper_review",
            {**second["data"], "review_hash": "", "checkpoint_hash": second["data"]["review_hash"]},
            identifier=row["id"],
            expected=second["revision"],
        )
    assert (
        json.loads(paper_reviews.export(store, legacy["id"])["content"])["snapshot"][
            "schema_version"
        ]
        == 2
    )


def test_parallel_saves_commit_their_own_json_in_order(context, tmp_path):
    store, edition = context
    row = create(store, edition)
    task, refs, sid, _, original = staged(tmp_path, row)
    barrier = threading.Barrier(2)

    def submit(title):
        data = deepcopy(original)
        data["comments"][0]["title"] = title
        barrier.wait(timeout=10)
        return paper_reviews.save_review(task, refs, sid, json.dumps(data))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(copy_context().run, submit, title)
            for title in ("First proposed edit", "Second proposed edit")
        ]
        results = [future.result(timeout=40) for future in futures]
    assert [row["data"]["issues"][0]["title"] for row in results] == [
        "First proposed edit",
        "Second proposed edit",
    ]
    ordered = sorted(results, key=lambda row: row["revision"])
    assert ordered[1]["data"]["previous_checkpoint_hash"] == ordered[0]["data"]["checkpoint_hash"]
    restored = json.loads(paper_reviews.read_review(task, refs, sid))
    assert restored["comments"][0]["title"] == ordered[1]["data"]["issues"][0]["title"]


def test_successful_manuscript_build_bridges_to_a_real_frozen_edition(context):
    store, edition = context
    with store.db(write=True) as db:
        manuscript = store.put(db, "manuscript", {"title": "Own HOI paper"})
        build = store.put(
            db,
            "build",
            {
                "manuscript_id": manuscript["id"],
                "status": "succeeded",
                "outputs": {"preview.pdf": edition["data"]["files"][0]["hash"]},
            },
        )
    command = paper_reviews.PaperReviewCreate(
        request_id="manuscript-review-request", manuscript_id=manuscript["id"], build_id=build["id"]
    )
    row = paper_reviews.create(store, command)
    ref = ObjectRef.model_validate(row["data"]["target_ref"])
    assert store.revision(ref.id, ref.revision)["kind"] == "edition"
    assert tutor_sources.read(ref)["blob_hash"] == ref.locator.blob_hash
    assert paper_reviews.create(store, command)["id"] == row["id"]


def test_untrusted_skill_shadow_cannot_execute_as_server_code(context, tmp_path, monkeypatch):
    from deeptutor.services.skill import execution

    store, edition = context
    row = create(store, edition)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "SKILL.md").write_text("Malicious override")
    monkeypatch.setattr(execution, "_source", lambda name: shadow)
    run = []
    monkeypatch.setattr(paper_reviews.subprocess, "run", lambda *a, **kw: run.append(a))
    ref = ObjectRef.model_validate(row["review_ref"])
    with pytest.raises(Conflict, match="pinned built-in"):
        paper_reviews.prepare_execution(
            tmp_path / "turn", {tutor_sources.source_id(ref): row["review_ref"]}
        )
    assert run == []
