#!/usr/bin/env python3
"""Exercise native paper review and continuation with public PDFs and Codex OAuth.

Uses a new isolated home; never imports examples into the user's Studio. The
receipt checks plumbing and evidence anchors, not scientific review quality.
"""

import argparse
import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import time
import uuid

from evaluate_continuity import seed, setup_paths, write_json


async def review_turn(app, review, prompt, report, name):
    session, turn = await app.start_turn(
        {
            "session_id": review["data"]["session_id"],
            "content": prompt,
            "capability": "chat",
            "auto_route": False,
            "language": "zh",
            "tools": [],
            "skills": ["best-paper-review"],
            "mcp": [],
            "studio_references": [review["data"]["target_ref"], review["review_ref"]],
            "client_submission_id": uuid.uuid4().hex,
        }
    )
    events = []
    started = time.monotonic()
    async with asyncio.timeout(900), aclosing(app.stream_turn(turn["id"])) as stream:
        async for event in stream:
            events.append(event)
            if event["type"] in {"tool_call", "tool_result", "done"}:
                meta = event.get("metadata") or {}
                print(
                    json.dumps(
                        {
                            "case": name,
                            "event": event["type"],
                            "tool": meta.get("tool_name"),
                            "status": meta.get("status"),
                        }
                    ),
                    flush=True,
                )
            if event["type"] == "wait_for_input":
                raise RuntimeError("The public-source acceptance unexpectedly required user input.")
    detail = await app.get_session(session["id"])
    done = next((e.get("metadata", {}) for e in reversed(events) if e["type"] == "done"), {})
    answer = next(
        (
            m.get("content", "")
            for m in detail["messages"]
            if m["role"] == "assistant" and m["id"] == done.get("assistant_message_id")
        ),
        "",
    )
    tools = [
        {
            "event": e["type"],
            "tool": e.get("metadata", {}).get("tool_name"),
            "metadata": e.get("metadata", {}).get("tool_metadata", {}),
        }
        for e in events
        if e["type"] in {"tool_call", "tool_result"}
    ]
    receipt = {
        "case": name,
        "session_id": session["id"],
        "turn_id": turn["id"],
        "status": done.get("status"),
        "answer": answer,
        "tools": tools,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    write_json(report / f"{name}.json", receipt)
    if done.get("status") != "completed":
        raise RuntimeError(f"{name} did not complete; inspect its receipt.")
    return receipt


async def run(home, sources, report):
    from deeptutor.app.container import close_application_container
    from deeptutor.app.facade import DeepTutorApp
    from deeptutor.services.codex_auth import get_codex_oauth_service
    from deeptutor.services.llm.config import get_llm_config
    from deeptutor.services.setup.init import init_user_directories
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.studio import paper_reviews
    from deeptutor.studio.database import Studio

    try:
        init_user_directories()
        fixtures = seed(sources, report)
        await get_codex_oauth_service().refresh_models()
        config = get_llm_config()
        if config.binding != "openai_codex":
            raise RuntimeError("Use the existing Codex OAuth login for this acceptance.")
        workspace = fixtures["workspaces"]["hoi"]
        edition = fixtures["editions"]["hoi_qpic"]
        with workspace_context(workspace):
            review = paper_reviews.create(
                Studio(),
                paper_reviews.PaperReviewCreate(
                    request_id=uuid.uuid4().hex,
                    edition_id=edition["id"],
                    revision=edition["revision"],
                    blob_hash=edition["data"]["files"][0]["hash"],
                    mode="quick",
                    venue="general",
                    language="zh",
                    instructions="以公开QPIC论文验收作者自查功能；没有私人稿件。快速检查方法和实验表达，不给录用概率。",
                ),
            )
            review = await paper_reviews.bind_session(Studio(), review["id"])
        app = DeepTutorApp(workspace_id=workspace)
        first = await review_turn(
            app,
            review,
            "请读取best-paper-review技能，并审阅附上的公开QPIC论文。这是quick模式的软件验收，"
            "请实际读第1页和至少一个方法或实验页；只提出2到3条适度、具体且有原文定位的作者修改建议。"
            "必须用paper_review_checkpoint action=read取得初始化JSON，再用action=save保存更新后的完整JSON。"
            "保留原有provenance/sources/paper hash；严格逐字引用，不要编造缺失内容。"
            "逐项注明未做外部检索、独立复核和视觉图表检查，维度没查就保持unchecked。"
            "无需询问，最后简短说明这份快速自查的局限，不要预测录用。",
            report,
            "quick-review",
        )
        with workspace_context(workspace):
            saved = paper_reviews.get(Studio(), review["id"])
            write_json(report / "checkpoint-first.json", saved)
        second = await review_turn(
            app,
            saved,
            "继续这次已保存的审稿。重新读取paper_review_checkpoint action=read中的JSON，"
            "选择第一条意见，回到它引用的原文页核对，将修改建议写得更具体，同时避免夸大其严重性。"
            "保持所有意见ID和来源不变，再用action=save保存完整JSON。不要宣称已修改论文。",
            report,
            "continued-review",
        )
        with workspace_context(workspace):
            final = paper_reviews.get(Studio(), review["id"])
            exported = paper_reviews.export(Studio(), review["id"], "markdown")
        write_json(report / "checkpoint-final.json", final)
        (report / "review.md").write_text(exported["content"], encoding="utf-8")
        checks = {
            "oauth": config.binding == "openai_codex",
            "same_session": first["session_id"]
            == second["session_id"]
            == final["data"]["session_id"],
            "skill_read": any(t["tool"] == "read_skill" for t in first["tools"]),
            "checkpoint_created": bool(saved["data"]["checkpoint_hash"]),
            "checkpoint_advanced": bool(final["data"]["checkpoint_hash"])
            and final["data"]["checkpoint_hash"] != saved["data"]["checkpoint_hash"],
            "version_chain": final["data"]["previous_checkpoint_hash"]
            == saved["data"]["checkpoint_hash"],
            "original_unchanged": final["data"]["target_ref"] == review["data"]["target_ref"],
            "located_issues": len(final["data"]["issues"]) >= 2
            and all(i["location_status"] == "matched" for i in final["data"]["issues"]),
            "incomplete_checks_visible": bool(final["data"]["pending"]),
            "human_review_required": final["data"]["checks"]["scientific_claims"]
            == "not_automatically_verified",
        }
        result = {
            "checks": checks,
            "passed": all(checks.values()),
            "model": {"binding": config.binding, "name": config.model},
            "review_id": review["id"],
            "workspace_id": workspace,
            "home": str(home),
            "scientific_review_quality": "requires_human_review",
        }
        write_json(report / "summary.json", result)
        print(json.dumps({"passed": result["passed"], "report": str(report)}), flush=True)
        if not result["passed"]:
            raise RuntimeError("Paper review acceptance checks failed.")
    finally:
        await close_application_container()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    home, sources, report = setup_paths(args)
    asyncio.run(run(home, sources, report))
