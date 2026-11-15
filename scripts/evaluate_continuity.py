#!/usr/bin/env python3
"""Evaluate DeepTutor learning and research continuity in a fresh runtime."""

import argparse
import asyncio
from contextlib import aclosing
import hashlib
from itertools import combinations
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid

REPO = Path(__file__).resolve().parents[1]
PUBLIC_SOURCES = (
    ("dsa_bfs", "MIT 6.006 — Breadth-First Search", "dsa"),
    ("dsa_dijkstra", "MIT 6.006 — Dijkstra", "dsa"),
    ("hoi_qpic", "QPIC: Query-Based Pairwise Human-Object Interaction Detection", "hoi"),
    (
        "hoi_gen",
        "GEN-VLKT: Simplify Association and Enhance Interaction Understanding for HOI Detection",
        "hoi",
    ),
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="New or empty scratch runtime")
    parser.add_argument("--sources", type=Path, required=True, help="Directory of four public PDFs")
    parser.add_argument(
        "--output", type=Path, required=True, help="Parent for a fresh report folder"
    )
    parser.add_argument(
        "--mode", choices=("all", "baseline", "sources", "practice", "resume"), default="all"
    )
    return parser.parse_args()


def setup_paths(args):
    home = args.home.expanduser().resolve()
    sources = args.sources.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if any(
        left.is_relative_to(right) or right.is_relative_to(left)
        for left, right in combinations((home, sources, output), 2)
    ):
        raise ValueError("--home, --sources, and --output must occupy separate directories.")

    previous_home = Path(os.environ.get("DEEPTUTOR_HOME") or Path.cwd()).expanduser().resolve()
    local_codex = (
        Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    )
    protected = ((previous_home / "data").resolve(), (REPO / "data").resolve(), local_codex)
    if home in {previous_home, REPO, Path.home().resolve()} or any(
        path.is_relative_to(root) for path in (home, output) for root in protected
    ):
        raise ValueError("Choose scratch locations outside your DeepTutor data and Codex login.")
    if home.exists() and (not home.is_dir() or any(home.iterdir())):
        raise ValueError("--home must be a new or empty directory for each run.")
    for name, _, _ in PUBLIC_SOURCES:
        filename = f"{name}.pdf"
        if not (sources / filename).is_file():
            raise ValueError(f"--sources is missing {filename}.")

    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    # An exclusive marker also prevents two runners from claiming the same empty directory.
    (home / ".continuity-run").mkdir(mode=0o700)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    report = Path(tempfile.mkdtemp(prefix="continuity-", dir=output))
    os.environ.update(DEEPTUTOR_HOME=str(home), DEEPTUTOR_USE_CODEX_LOGIN="1")
    sys.path.insert(0, str(REPO))
    return home, sources, report


def write_json(path, result):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_case(report, name, checks, **details):
    receipt = {"case": name, "checks": checks, **details}
    receipt["checks_passed"] = all(checks.values())
    write_json(report / f"{name}.json", receipt)
    print(
        json.dumps({"case": name, "checks_passed": receipt["checks_passed"]}),
        flush=True,
    )


def seed(sources, report):
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.service import get_content_workspace_service
    from deeptutor.studio import catalog
    from deeptutor.studio.database import Studio
    from deeptutor.studio.models import EditionInput, WorkInput

    service = get_content_workspace_service()
    workspaces = {
        key: service.create_workspace(title)["workspace_id"]
        for key, title in (
            ("dsa", "数据结构与算法 · 连续学习验收"),
            ("hoi", "HOI · 科研连续性验收"),
            ("isolated", "隔离校验项目"),
        )
    }
    editions = {}
    sources_manifest = {}
    for name, title, project in PUBLIC_SOURCES:
        filename = f"{name}.pdf"
        pdf = (sources / filename).read_bytes()
        with workspace_context(workspaces[project]):
            store = Studio()
            work = catalog.create_work(store, WorkInput(title=title))
            row = catalog.create_edition(
                store, EditionInput(work_id=work["id"], label="Public source PDF")
            )
            row = catalog.attach_pdf(store, row["id"], pdf, filename, row["revision"])
        editions[name] = row
        sources_manifest[filename] = {
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "edition_id": row["id"],
            "revision": row["revision"],
        }
    write_json(report / "fixtures.json", {"workspaces": workspaces, "sources": sources_manifest})
    return {"workspaces": workspaces, "editions": editions}


def reference(fixtures, name, page):
    row = fixtures["editions"][name]
    return {
        "owner": "current_account",
        "scope": "@library",
        "kind": "edition",
        "id": row["id"],
        "revision": row["revision"],
        "locator": {"page": page, "blob_hash": row["data"]["files"][0]["hash"]},
    }


def matches_citation(citation, ref, workspace):
    href = urlsplit(citation.get("href", ""))
    arguments = parse_qs(href.query)
    route = "library" if ref["kind"] == "edition" else "objects"
    if (
        href.scheme
        or href.netloc
        or href.path != f"/{route}/{ref['id']}"
        or arguments.get("dt_workspace") != [workspace]
        or arguments.get("revision") != [str(ref["revision"])]
        or citation.get("kind") != ref["kind"]
        or citation.get("revision") != ref["revision"]
    ):
        return False
    locator = ref.get("locator", {})
    if ref["kind"] == "edition":
        return (
            citation.get("page") == locator["page"]
            and citation.get("blob_hash") == locator["blob_hash"]
            and arguments.get("file") == [locator["blob_hash"]]
            and arguments.get("page") == [str(locator["page"])]
        )
    line = citation.get("line") or 0
    return 1 <= line <= 3 <= (citation.get("end_line") or 0) and arguments.get("line") == [
        str(line)
    ]


async def turn(
    app, report, name, prompt, checklist, *, session_id=None, refs=(), expected=(), questions=()
):
    started = time.monotonic()
    payload = {
        "content": prompt,
        "capability": "chat",
        "language": "zh",
        "tools": [],
        "auto_route": False,
        "client_submission_id": uuid.uuid4().hex,
    }
    if session_id:
        payload["session_id"] = session_id
    if refs:
        payload["studio_references"] = list(refs)
    if questions:
        payload["question_notebook_references"] = list(questions)
    session, row = await app.start_turn(payload)
    events = []
    async with asyncio.timeout(300), aclosing(app.stream_turn(row["id"])) as stream:
        async for event in stream:
            events.append(event)
            if event["type"] == "wait_for_input":
                raise RuntimeError(f"{name} unexpectedly required user input.")

    done = next((e["metadata"] for e in reversed(events) if e["type"] == "done"), {})
    detail = await app.get_session(session["id"])
    assistant = next(
        (
            m
            for m in detail["messages"]
            if m["role"] == "assistant" and m["id"] == done.get("assistant_message_id")
        ),
        {},
    )
    answer = assistant.get("content") or ""
    status = done.get("status")
    answer_links = set(re.findall(r"\]\((/[^()\s]+)\)", answer))
    tools = []
    citations = []
    for event in events:
        if event["type"] not in {"tool_call", "tool_result"}:
            continue
        metadata = event.get("metadata") or {}
        type_name = event["type"]
        tools.append(
            {
                "event": type_name,
                "seq": event.get("seq"),
                "tool": metadata.get("tool_name"),
                "call_id": metadata.get("tool_call_id"),
            }
        )
        citation = (metadata.get("tool_metadata") or {}).get("studio_citation")
        if (
            type_name == "tool_result"
            and metadata.get("tool_name") == "read_studio_source"
            and citation
        ):
            fields = (
                "kind",
                "revision",
                "href",
                "page",
                "line",
                "end_line",
                "blob_hash",
                "source_id",
            )
            citations.append(
                {"seq": event.get("seq"), **{k: citation[k] for k in fields if k in citation}}
            )

    checks = {"stream_completed": status == "completed", "persisted_answer": bool(answer.strip())}
    required_reads = tuple(expected or refs)
    if required_reads:
        matches = [
            [c for c in citations if matches_citation(c, ref, app.workspace_id)]
            for ref in required_reads
        ]
        checks["requested_revisions_and_locations_read"] = all(matches)
        checks["answer_links_to_required_reads"] = all(
            any(c["href"] in answer_links for c in group) for group in matches
        )
    if session_id:
        checks["continued_same_session"] = session["id"] == session_id
    write_case(
        report,
        name,
        checks,
        session_id=session["id"],
        workspace_id=app.workspace_id,
        turn_id=row["id"],
        assistant_message_id=assistant.get("id"),
        stream_status=status,
        elapsed_seconds=round(time.monotonic() - started, 2),
        attached_sources=list(refs),
        expected_reads=list(required_reads),
        question_notebook_references=list(questions),
        tools=tools,
        citations=citations,
        answer_citation_links=sorted(answer_links.intersection(c["href"] for c in citations)),
        prompt=prompt,
        answer=answer,
        answer_review={"status": "needs_human_review", "checks": checklist},
    )
    if status != "completed":
        raise RuntimeError(f"{name} did not complete. See its receipt.")
    return session["id"]


def remote_fixture(home, workspace, report):
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.studio import connections, remote_reader
    from deeptutor.studio.connections import ConnectionInput, ReadRequest, bounded_process
    from deeptutor.studio.database import Studio

    path = home / "simulated-ssh"
    path.mkdir(mode=0o700)
    log = path / "train.log"
    initial = (
        "SIMULATED ACCEPTANCE LOG — no experiment was executed.\n"
        "epoch=1 mAP=23.4\n"
        "epoch=2 mAP=24.1\n"
        "checkpoint=/fixture/model.pth\n"
    )
    log.write_text(initial, encoding="utf-8")

    def local(config, request):
        code, raw, _ = bounded_process(
            [sys.executable, "-I", remote_reader.__file__],
            json.dumps({**request.model_dump(), "roots": config["roots"]}).encode(),
        )
        if code:
            raise RuntimeError("The simulated SSH protocol subprocess failed.")
        payload = json.loads(raw)
        if not payload["ok"]:
            raise RuntimeError("The simulated SSH protocol rejected its fixture.")
        return payload["result"]

    with workspace_context(workspace):
        store = Studio()
        connection = connections.create_connection(
            store,
            ConnectionInput(
                title="本地 SSH 模拟 · 未执行实验",
                provider="ssh",
                alias="acceptance-only",
                roots=[str(path)],
            ),
        )
        snapshot = connections.read_connection(
            store, connection["id"], ReadRequest(operation="read", path=str(log)), transport=local
        )
        log.write_text("changed after the snapshot\n", encoding="utf-8")
        preserved = connections.snapshot_text(store, snapshot["id"])["text"] == initial
    ref = {
        "owner": "current_account",
        "scope": workspace,
        "kind": "remote_snapshot",
        "id": snapshot["id"],
        "revision": snapshot["revision"],
    }
    write_case(
        report,
        "ssh-fixture",
        {"snapshot_unchanged_after_local_edit": preserved},
        reference=ref,
        transport="simulated: local protocol subprocess; no SSH connection or experiment",
    )
    return ref


async def source_workflows(fixtures, home, report):
    from deeptutor.app.facade import DeepTutorApp

    app = DeepTutorApp(workspace_id=fixtures["workspaces"]["dsa"])
    bfs = reference(fixtures, "dsa_bfs", 4)
    session_id = await turn(
        app,
        report,
        "dsa-page-explanation",
        "请先读取我选定的 BFS 讲义原页。我的理解是：用栈与用队列一样，都能保证第一次到达一个点时就是最短路径。"
        "请结合这一页指出我的错误，再给一个小反例。保留可回到原页的来源链接，最多250字。",
        ["Explains FIFO layer order and gives a valid counterexample to the stack claim."],
        refs=[bfs],
    )
    await turn(
        app,
        report,
        "dsa-continuous-followup",
        "那我改成这样：只有每条边权都相同，队列按层扩展才保证最短路径。"
        "请根据刚才那份讲义核对我的改正，并留一道不含答案的迁移问题。",
        [
            "Qualifies the shortest-path guarantee and leaves a relevant transfer exercise unanswered."
        ],
        session_id=session_id,
        expected=[bfs],
    )

    app = DeepTutorApp(workspace_id=fixtures["workspaces"]["hoi"])
    session_id = await turn(
        app,
        report,
        "hoi-multi-paper",
        "请实际读取两篇所选论文，比较 QPIC 和 GEN-VLKT 的配对机制与外部知识。"
        "我怀疑‘QPIC 也用了 CLIP 蒸馏’这一说法不对，请核查。"
        "两篇分别给出可回到原页的链接；缺少证据时明确说明，最多450字。",
        [
            "Explains each pairing mechanism using the correct paper and attributes CLIP to GEN-VLKT."
        ],
        refs=[reference(fixtures, "hoi_qpic", 3), reference(fixtures, "hoi_gen", 5)],
    )
    await turn(
        app,
        report,
        "hoi-page-followup",
        "继续核查 GEN-VLKT 的消融表：+mimic 的 Full mAP 是否为33.75？"
        "请打开对应原页再判断，给出表号与物理页链接。"
        "这个数能否直接证明比任何不同 backbone 的 QPIC 实验更好？",
        [
            "Identifies 33.75 Full mAP at Table 4(b) on physical page 9.",
            "Explains the scope of the comparison, including configuration and protocol limits.",
        ],
        session_id=session_id,
        expected=[reference(fixtures, "hoi_gen", 9)],
    )
    snapshot = remote_fixture(home, fixtures["workspaces"]["hoi"], report)
    await turn(
        app,
        report,
        "hoi-local-ssh-snapshot",
        "请实际读取我附上的日志快照。报告最后一条 mAP，并回答："
        "这是否证明我的 HOI 实验已成功运行、比 QPIC 更好？"
        "请区分日志字节、模拟资料标识、缺失的运行身份与可比协议。需要带快照行号链接。",
        [
            "Reports 24.1 as a value in a synthetic log and identifies missing execution evidence.",
            "Explains what would be needed for a meaningful comparison to QPIC.",
        ],
        refs=[snapshot],
    )


async def practice_workflow(fixtures, report):
    from fastapi import FastAPI
    import httpx

    from deeptutor.api.routers import practice
    from deeptutor.app.facade import DeepTutorApp
    from deeptutor.learning.models import (
        KnowledgePoint,
        KnowledgeType,
        LearningModule,
        LearningProgress,
        TopicMetadata,
    )
    from deeptutor.learning.storage import LearningStore
    from deeptutor.services.practice.projection import reconcile_practice_evidence
    from deeptutor.services.practice.scheduler import DAY
    from deeptutor.services.practice.storage import PracticeStore
    from deeptutor.services.session import get_sqlite_session_store
    from deeptutor.services.workspace.context import workspace_context

    workspace = fixtures["workspaces"]["dsa"]
    marker = uuid.uuid4().hex[:12]
    path_id = "dsa-" + marker
    prefix = "/api/question-notebook/practice"
    api = FastAPI()
    api.include_router(practice.router, prefix=prefix)
    with workspace_context(workspace):
        learning = LearningStore()
        learning.save(
            LearningProgress(
                book_id=path_id,
                name="数据结构与算法 · 反馈验收",
                modules=[
                    LearningModule(
                        id="graphs",
                        name="图算法",
                        order=0,
                        knowledge_points=[
                            KnowledgePoint(
                                id="bfs",
                                module_id="graphs",
                                name="BFS 的队列与分层性质",
                                type=KnowledgeType.CONCEPT,
                            )
                        ],
                    )
                ],
                knowledge_types={"bfs": KnowledgeType.CONCEPT},
                mastery_levels={"bfs": 0.35},
            )
        )
        learning.put_topic(TopicMetadata(path_id=path_id, goal="理解 BFS 的适用条件"), [])
        store = get_sqlite_session_store()
        await store.ensure_notebook_session(
            "practice-fixture-" + marker, "本地验收样题（人工编写）"
        )
        await store.upsert_notebook_entries(
            "practice-fixture-" + marker,
            [
                {
                    "question_id": "bfs-structure",
                    "question": "要保证无权图中 BFS 按距离分层扩展，应使用哪种结构？",
                    "question_type": "single_choice",
                    "options": {
                        "A": "栈",
                        "B": "队列",
                        "C": "按顶点编号排序的最小堆",
                        "D": "递归调用栈",
                    },
                    "correct_answer": "B",
                    "explanation": "队列先进先出，使本层顶点先于下一层扩展。",
                    "user_answer": "A",
                    "is_correct": False,
                    "source": "immersive_reading",
                }
            ],
        )
        entry = await store.find_notebook_entry("practice-fixture-" + marker, "bfs-structure", "")
        url = prefix + f"/questions/{entry['id']}"
        checks = {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api), base_url="http://acceptance.invalid"
        ) as client:

            async def request(method, suffix="", body=None):
                response = await client.request(method, url + suffix, json=body)
                response.raise_for_status()
                return response.json()

            stem = await request("GET")
            checks["answer_hidden_before_check"] = not stem["entry"]["correct_answer"]
            linked = await request(
                "PUT",
                "/objective",
                {
                    "version": stem["state"]["version"],
                    "objective": {"path_id": path_id, "knowledge_point_id": "bfs"},
                },
            )
            attempt = {
                "request_id": "acceptance-wrong-" + marker,
                "version": linked["state"]["version"],
                "answer": "C",
            }
            checked = await request("POST", "/check", attempt)
            checks["wrong_answer_detected"] = checked["correct"] is False
            wrong = await request("POST", "/review", {**attempt, "rating": "again"})
            duplicate = await request("POST", "/review", {**attempt, "rating": "again"})
            checks["duplicate_review_idempotent"] = wrong == duplicate
            checks["first_attempt_independent_and_projected"] = (
                wrong["independent"] and wrong["objective_evidence"] == "applied"
            )
            app = DeepTutorApp(workspace_id=workspace)
            session_id = await turn(
                app,
                report,
                "dsa-practice-diagnosis",
                "请根据这道题最近保存的练习作答，准确说出我这次选的是哪个选项，再解释错误原因，给一个不含答案的相似迁移题。不要误用题库的最初历史答案。最多250字。",
                [
                    "Uses the latest saved choice C, explains the reasoning error, and leaves a transfer question unanswered."
                ],
                questions=[entry["id"]],
            )
            correction = {
                "request_id": "acceptance-corrected-" + marker,
                "version": wrong["version"],
                "answer": "B",
            }
            await request("POST", "/check", correction)
            corrected = await request("POST", "/review", {**correction, "rating": "good"})
            checks["immediate_correction_not_independent"] = (
                not corrected["independent"] and corrected["objective_evidence"] == "not_applicable"
            )
            await turn(
                app,
                report,
                "dsa-practice-followup",
                "我已经在练习里更正并保存了答案。请再读取这道题最新的保存记录：我现在选了什么？解释这次是否能证明我已经独立掌握，不要把旧错误当成本轮答案。",
                [
                    "Uses latest saved choice B and distinguishes correction from independent mastery."
                ],
                session_id=session_id,
                questions=[entry["id"]],
            )
        # Delayed retention is an explicit clock simulation, not elapsed real time.
        repo = PracticeStore(store.db_path)
        future = time.time() + DAY + 5
        delayed_id = "acceptance-delayed-" + marker
        repo.check(entry["id"], "B", delayed_id, corrected["version"], now=future)
        delayed = repo.review(
            entry["id"], delayed_id, corrected["version"], "good", "B", now=future + 1
        )
        reconcile_practice_evidence(repo)
        progress = learning.load(path_id)

        checks["delayed_answer_independent_and_projected"] = (
            delayed["independent"] and repo.evidence_status(delayed_id) == "applied"
        )
        checks["mastery_unchanged"] = (
            progress.mastery_levels["bfs"] == 0.35 and not progress.qualitative_mastery
        )
        checks["two_independent_evidence_events"] = len(progress.learning_evidence) == 2
        write_case(
            report,
            "practice-retention",
            checks,
            workspace_id=workspace,
            question_entry_id=entry["id"],
            path_id=path_id,
            authorship="hand-authored BFS acceptance question",
            delayed_clock="simulated: scheduler clock advanced by one day",
            evidence_count=len(progress.learning_evidence),
            reviews={"wrong": wrong, "corrected": corrected, "delayed": delayed},
        )


def resume_workflow(fixtures, report):
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.studio import resume
    from deeptutor.studio.contracts import ObjectRef
    from deeptutor.studio.database import Studio

    workspace = fixtures["workspaces"]["dsa"]
    other_workspace = fixtures["workspaces"]["isolated"]
    ref = ObjectRef.model_validate(reference(fixtures, "dsa_bfs", 4))
    with workspace_context(workspace):
        saved = resume.save(Studio(), ref)
        items = resume.pending(Studio())
    with workspace_context(other_workspace):
        other_items = resume.pending(Studio())
    item = next((row for row in items if row["id"] == saved["id"]), None)
    write_case(
        report,
        "dsa-reading-resume",
        {
            "saved_physical_page_retained": (
                item is not None and item["source"] == ref.model_dump()
            ),
            "continuation_stays_in_workspace": all(row["id"] != saved["id"] for row in other_items),
        },
        workspace_id=workspace,
        other_workspace_id=other_workspace,
        continuation=item,
    )


async def run(mode, home, sources, report):
    from deeptutor.app.container import close_application_container
    from deeptutor.app.facade import DeepTutorApp
    from deeptutor.services.setup.init import init_user_directories

    engine = None
    completed = False
    try:
        init_user_directories()
        fixtures = seed(sources, report)
        if mode != "resume":
            from deeptutor.services.codex_auth import get_codex_oauth_service
            from deeptutor.services.llm.config import get_llm_config

            await get_codex_oauth_service().refresh_models()
            config = get_llm_config()
            if config.binding != "openai_codex":
                raise RuntimeError("This evaluation requires the existing local Codex OAuth login.")
            engine = {"binding": config.binding, "model": config.model}
            print(json.dumps(engine), flush=True)
        if mode in {"all", "baseline"}:
            await turn(
                DeepTutorApp(workspace_id=fixtures["workspaces"]["dsa"]),
                report,
                "baseline-bfs",
                "数据结构与算法验收：无权图边为 s-a, s-b, a-c, b-c, c-t。"
                "BFS 从 s 到 t 的距离是多少？简述第一次发现节点就是最短路径的原因。最多120字。",
                ["Computes distance 3 and explains why BFS discovers nodes in distance order."],
            )
        if mode in {"all", "sources"}:
            await source_workflows(fixtures, home, report)
        if mode in {"all", "practice"}:
            await practice_workflow(fixtures, report)
        if mode in {"all", "resume"}:
            resume_workflow(fixtures, report)
        completed = True
    finally:
        await close_application_container()
        receipts = [
            json.loads(file.read_text(encoding="utf-8"))
            for file in sorted(report.glob("*.json"))
            if file.name != "fixtures.json"
        ]
        structural_ok = (
            completed and bool(receipts) and all(row["checks_passed"] for row in receipts)
        )
        write_json(
            report / "summary.json",
            {
                "schema_version": 1,
                "mode": mode,
                "suite_state": "finished" if completed else "interrupted",
                "model": engine,
                "automated_checks_passed": structural_ok,
                "cases": [
                    {
                        "case": row["case"],
                        "receipt": f"{row['case']}.json",
                        "checks_passed": row["checks_passed"],
                        "answer_review": row.get("answer_review"),
                    }
                    for row in receipts
                ],
            },
        )
    return structural_ok


def main():
    args = parse_args()
    try:
        home, sources, report = setup_paths(args)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Report directory: {report}", flush=True)
    return 0 if asyncio.run(run(args.mode, home, sources, report)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
