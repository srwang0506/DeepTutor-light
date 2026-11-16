"""Review handoff stays scoped to trusted turn state, including without exec."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deeptutor.agents._shared.tool_composition import ToolMountFlags, compose_enabled_tools
from deeptutor.agents._shared.tool_runtime import bind_workspace_tool_runtime
from deeptutor.core.context import UnifiedContext, WorkspaceRuntimeContext
from deeptutor.services.skill import execution, runtime
from deeptutor.services.skill.service import SkillNotFoundError, SkillService
from deeptutor.tools.builtin import ReadSkillTool
from deeptutor.tools.paper_review import PaperReviewCheckpointTool


def context_at(tmp_path):
    root = tmp_path / "workspace"
    task = root / "outputs/turn"
    task.mkdir(parents=True)
    context = UnifiedContext()
    context.runtime.workspace = WorkspaceRuntimeContext(
        workspace_id="ws-owned", root=str(root), output_dir=str(task)
    )
    return context, task


@pytest.fixture
def prepared_env(monkeypatch):
    monkeypatch.setattr(
        "deeptutor.services.workspace.execution.prepare_workspace_execution_env",
        lambda *_args, **_kwargs: {"PATH": "/trusted/bin"},
    )


def use_skill(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    package = root / "demo"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\nRead me.")
    (package / "reference.md").write_text("Just a reference.")
    service = SkillService(root=root, builtin_root=None)
    monkeypatch.setattr(runtime, "runtime_skills", lambda: {"demo": service})
    return service


@pytest.mark.asyncio
async def test_only_successfully_read_skills_mount_and_grants_are_rechecked(
    tmp_path, monkeypatch, prepared_env
):
    use_skill(tmp_path, monkeypatch)
    context, task = context_at(tmp_path)
    tool = ReadSkillTool()
    for file in ("missing.md", "reference.md"):
        kwargs = bind_workspace_tool_runtime("read_skill", {"name": "demo", "file": file}, context)
        await tool.execute(**kwargs)
        execution_args = bind_workspace_tool_runtime("exec", {}, context)
        assert "DEEPTUTOR_SKILLS_DIR" not in execution_args["_sandbox_env"]
        assert not any(
            "/skills/demo" in item.host_path for item in execution_args["_sandbox_mounts"]
        )
    # Files on disk, or a model-supplied success set, are not evidence of a read.
    kwargs = bind_workspace_tool_runtime(
        "read_skill",
        {
            "name": "demo",
            "_execution_skill_reads": {"unread"},
            "_execution_skill_path": "/outside/demo",
        },
        context,
    )
    assert (
        kwargs["_execution_skill_reads"] is context.runtime.loop_extensions["read_execution_skills"]
    )
    assert kwargs["_execution_skill_path"] == str(task / ".deeptutor/skills/demo")
    assert (await tool.execute(**kwargs)).success
    execution_args = bind_workspace_tool_runtime("exec", {}, context)
    assert execution_args["_sandbox_env"]["DEEPTUTOR_SKILLS_DIR"] == str(task / ".deeptutor/skills")
    mounts = [
        item for item in execution_args["_sandbox_mounts"] if "/skills/demo" in item.host_path
    ]
    assert len(mounts) == 1 and mounts[0].read_only
    assert mounts[0].sandbox_path == mounts[0].host_path
    monkeypatch.setattr(runtime, "runtime_skills", lambda: {})
    with pytest.raises(SkillNotFoundError):
        bind_workspace_tool_runtime("exec", {}, context)


def test_execution_context_replaces_untrusted_paths_mounts_and_environment(tmp_path, prepared_env):
    context, task = context_at(tmp_path)
    supplied = {
        "_sandbox_env": {"DEEPTUTOR_SKILLS_DIR": "/untrusted", "SECRET": "injected"},
        "_sandbox_workdir": "/untrusted",
        "_sandbox_code_workdir": "/untrusted",
        "_sandbox_source_dir": "/untrusted",
        "_sandbox_mounts": ["untrusted"],
        "_sandbox_internal_root": "/untrusted",
        "_sandbox_user_id": "another-owner",
    }
    result = bind_workspace_tool_runtime("exec", supplied, context, sandbox_user_id="current-owner")
    assert supplied["_sandbox_workdir"] == "/untrusted"
    assert result["_sandbox_workdir"] == str(task / "exec")
    assert result["_sandbox_code_workdir"] == str(task / "exec")
    assert result["_sandbox_source_dir"] == str(task / ".deeptutor/execution/exec_calls")
    assert result["_sandbox_internal_root"] == str(task / ".deeptutor/execution")
    assert result["_sandbox_user_id"] == "current-owner"
    assert result["_sandbox_env"] == {"PATH": "/trusted/bin"}
    assert result["_sandbox_mounts"][0].read_only
    assert all("untrusted" not in item.host_path for item in result["_sandbox_mounts"])


def test_pipeline_replaces_checkpoint_target_and_source_refs(tmp_path):
    from deeptutor.agents.loop.pipeline import AgenticLoopPipeline

    context, task = context_at(tmp_path)
    refs = {"R1": {"kind": "paper_review", "id": "owned"}}
    context.metadata["studio_source_refs"] = refs
    pipeline = SimpleNamespace(
        _current_user_id=lambda: "current-owner", _active_loop_capabilities=lambda _: ()
    )
    kwargs = AgenticLoopPipeline._augment_tool_kwargs(
        pipeline,
        "paper_review_checkpoint",
        {
            "source_id": "R1",
            "_paper_review_task_dir": "/another-owner",
            "_studio_source_refs": {"R1": {"id": "forged"}},
        },
        context,
    )
    assert kwargs["_paper_review_task_dir"] == str(task)
    assert kwargs["_studio_source_refs"] is refs
    assert kwargs["source_id"] == "R1"


def test_unstaged_read_clears_a_model_execution_location():
    result = bind_workspace_tool_runtime(
        "read_skill",
        {"name": "demo", "_execution_skill_path": "/another-owner/demo"},
        UnifiedContext(),
    )
    assert "_execution_skill_path" not in result


@pytest.mark.parametrize(
    "has_review,has_exec,whitelist,expected",
    [
        (False, True, None, False),
        (True, False, None, True),
        (True, True, set(), False),
        (True, False, {"paper_review_checkpoint"}, True),
    ],
)
def test_review_tool_gate_is_independent_of_generic_execution(
    has_review, has_exec, whitelist, expected
):
    names = compose_enabled_tools(
        registry=SimpleNamespace(get_enabled=lambda _: []),
        requested_tools=[],
        optional_whitelist=[],
        mount_flags=ToolMountFlags(has_paper_review=has_review, has_exec=has_exec),
        builtin_whitelist=whitelist,
    )
    assert ("paper_review_checkpoint" in names) is expected
    if not has_exec:
        assert "exec" not in names


def test_review_execution_requires_a_read_playbook_and_readonly_inputs(
    tmp_path, monkeypatch, prepared_env
):
    context, task = context_at(tmp_path)
    refs = {"R1": {"kind": "paper_review", "id": "owned"}}
    context.metadata["studio_source_refs"] = refs
    package = task / ".deeptutor/skills/best-paper-review"
    called = []

    def prepare(directory, received_refs):
        called.append(directory)
        assert received_refs is refs
        return {
            "env": {"DEEPTUTOR_PAPER_REVIEW_DIR": str(task / "exec/paper-review")},
            "read_only_paths": [task / ".deeptutor/paper-review-input", package],
        }

    monkeypatch.setattr("deeptutor.studio.paper_reviews.prepare_execution", prepare)
    monkeypatch.setattr(
        execution, "stage_skill", lambda directory, name: directory / ".deeptutor/skills" / name
    )
    with pytest.raises(ValueError, match="[Rr]ead.*best-paper-review"):
        bind_workspace_tool_runtime("exec", {}, context)
    assert not called
    context.runtime.loop_extensions["read_execution_skills"] = {"best-paper-review"}
    result = bind_workspace_tool_runtime("exec", {}, context)
    assert called == [task]
    assert result["_sandbox_env"]["DEEPTUTOR_PAPER_REVIEW_DIR"] == str(task / "exec/paper-review")
    review_mounts = [
        item
        for item in result["_sandbox_mounts"]
        if item.host_path in {str(package), str(task / ".deeptutor/paper-review-input")}
    ]
    assert review_mounts and all(item.read_only for item in review_mounts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refs,sid",
    [
        ({}, "R1"),
        ({"R1": {"kind": "edition"}}, "R1"),
        ({"R1": None}, "R1"),
        ({"R1": {"kind": "paper_review"}}, "unknown"),
    ],
)
async def test_checkpoint_rejects_unattached_or_wrong_refs(tmp_path, monkeypatch, refs, sid):
    for helper in ("read_review", "save_review", "checkpoint"):
        monkeypatch.setattr(
            f"deeptutor.studio.paper_reviews.{helper}", lambda *_: pytest.fail("must not delegate")
        )
    result = await PaperReviewCheckpointTool().execute(
        action="read", source_id=sid, _studio_source_refs=refs, _paper_review_task_dir=str(tmp_path)
    )
    assert not result.success


@pytest.fixture
def native_review(tmp_path, monkeypatch):
    task = tmp_path / "turn"
    raw = '{"comments": [], "policy": {"not_done": ["Visual inspection"]}}'
    row = {
        "id": "review-owned",
        "revision": 4,
        "data": {
            "checkpoint_hash": "a" * 64,
            "issues": [{"body": "A long review body that does not belong in a receipt."}],
            "checks": {"scientific_claims": "not_automatically_verified"},
            "pending": ["Visual inspection"],
            "validation_report": {"errors": ["full mode has unchecked dimensions"], "warnings": []},
            "files": {"review.json": "a" * 64},
        },
    }
    helpers = {
        "read_review": Mock(return_value=raw),
        "save_review": Mock(return_value=row),
        "checkpoint": Mock(return_value=row),
    }
    for name, helper in helpers.items():
        monkeypatch.setattr(f"deeptutor.studio.paper_reviews.{name}", helper)
    kwargs = {
        "source_id": "R1",
        "_studio_source_refs": {"R1": {"kind": "paper_review"}},
        "_paper_review_task_dir": str(task),
    }
    return task, raw, row, helpers, kwargs


@pytest.mark.asyncio
async def test_native_read_delegates_to_scoped_atomic_reader_without_exec(native_review):
    task, raw, _, helpers, kwargs = native_review
    result = await PaperReviewCheckpointTool().execute(action="read", **kwargs)
    assert result.success and result.content == raw
    helpers["read_review"].assert_called_once_with(task, kwargs["_studio_source_refs"], "R1")
    helpers["save_review"].assert_not_called()
    helpers["checkpoint"].assert_not_called()
    assert not task.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["save", "checkpoint"])
async def test_native_save_and_checkpoint_delegate_once_and_return_only_a_receipt(
    native_review, action
):
    task, raw, row, helpers, kwargs = native_review
    result = await PaperReviewCheckpointTool().execute(action=action, review_json=raw, **kwargs)
    assert result.success
    chosen = "save_review" if action == "save" else "checkpoint"
    expected = (
        (task, kwargs["_studio_source_refs"], "R1", raw)
        if action == "save"
        else (task, kwargs["_studio_source_refs"], "R1")
    )
    helpers[chosen].assert_called_once_with(*expected)
    for name, helper in helpers.items():
        if name != chosen:
            helper.assert_not_called()
    assert not task.exists(), "The tool must leave all file operations to the atomic domain helper"
    receipt = json.loads(result.content)
    assert receipt == {
        "review_id": row["id"],
        "revision": row["revision"],
        "checkpoint_hash": row["data"]["checkpoint_hash"],
        "issues_count": 1,
        "checks": row["data"]["checks"],
        "pending": ["Visual inspection"],
        "validation_report": row["data"]["validation_report"],
    }
    assert result.metadata["paper_review_checkpoint"] == receipt
    assert "files" not in receipt and "issues" not in receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["read", "save", "checkpoint"])
@pytest.mark.parametrize(
    "error", [ValueError("Invalid review JSON or source"), PermissionError("Review access denied")]
)
async def test_atomic_domain_rejections_never_become_success_receipts(native_review, action, error):
    task, raw, _, helpers, kwargs = native_review
    chosen = {"read": "read_review", "save": "save_review", "checkpoint": "checkpoint"}[action]
    helpers[chosen].side_effect = error
    result = await PaperReviewCheckpointTool().execute(action=action, review_json=raw, **kwargs)
    assert not result.success
    assert result.content == str(error)
    assert "paper_review_checkpoint" not in result.metadata
    helpers[chosen].assert_called_once()
    for name, helper in helpers.items():
        if name != chosen:
            helper.assert_not_called()
    assert not task.exists()
