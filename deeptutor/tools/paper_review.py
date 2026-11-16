"""Commit a review artifact from the current turn's server-owned execution area."""

import asyncio
import json
from pathlib import Path
from typing import Any

from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult


class PaperReviewCheckpointTool(BaseTool):
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="paper_review_checkpoint",
            description=(
                "Read or save the structured review of an attached frozen manuscript. "
                "Use its exact source_id from [Attached Studio Sources]. Read best-paper-review "
                "first. action=read returns the initialized upstream review.json; action=save "
                "accepts its complete updated JSON string in review_json. No general script "
                "executor is needed: use read_studio_source for manuscript pages. If exec is "
                "available, work in DEEPTUTOR_PAPER_REVIEW_DIR and action=checkpoint saves it. "
                "This validates original "
                "quotes and records unfinished checks; passing checks is not scientific proof. "
                "Call after each substantive review or revision so the reader can resume it. "
                "No arbitrary paths, overwriting manuscript source, or external publication."
            ),
            parameters=[
                ToolParameter("source_id", "string", "Attached paper_review source ID."),
                ToolParameter(
                    "action",
                    "string",
                    "Read, save JSON, or checkpoint exec output.",
                    required=False,
                    enum=["read", "save", "checkpoint"],
                ),
                ToolParameter(
                    "review_json",
                    "string",
                    "Complete updated review JSON for action=save.",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        from deeptutor.services.workspace import WorkspaceError
        from deeptutor.studio.paper_reviews import checkpoint, read_review, save_review

        task_dir = kwargs.get("_paper_review_task_dir")
        refs = kwargs.get("_studio_source_refs")
        sid = str(kwargs.get("source_id") or "")
        if not task_dir or not isinstance(refs, dict) or sid not in refs:
            return ToolResult(
                content="Attach a paper review in this workspace first.", success=False
            )
        try:
            action = kwargs.get("action") or "checkpoint"
            if action not in {"read", "save", "checkpoint"}:
                raise ValueError("Choose read, save, or checkpoint.")
            if not isinstance(refs[sid], dict) or refs[sid].get("kind") != "paper_review":
                raise ValueError("Choose an attached paper_review source.")
            if action == "read":
                raw = await asyncio.to_thread(read_review, Path(task_dir), refs, sid)
                return ToolResult(content=raw)
            if action == "save":
                result = await asyncio.to_thread(
                    save_review, Path(task_dir), refs, sid, kwargs.get("review_json")
                )
            else:
                result = await asyncio.to_thread(checkpoint, Path(task_dir), refs, sid)
        except (ValueError, OSError, PermissionError, WorkspaceError) as exc:
            return ToolResult(content=str(exc), success=False)
        data = result["data"]
        receipt = {
            "review_id": result["id"],
            "revision": result["revision"],
            "checkpoint_hash": data["checkpoint_hash"],
            "issues_count": len(data["issues"]),
            "checks": data["checks"],
            "pending": data["pending"],
            "validation_report": data.get("validation_report", {}),
        }
        return ToolResult(
            content=json.dumps(receipt, ensure_ascii=False),
            metadata={"paper_review_checkpoint": receipt},
        )
