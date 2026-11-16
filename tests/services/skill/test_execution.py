from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deeptutor.services.skill import execution, runtime
from deeptutor.services.skill.service import (
    BUILTIN_SKILLS_ROOT,
    InvalidSkillNameError,
    SkillNotFoundError,
    SkillService,
)


def use_sources(monkeypatch, *sources, selected=None):
    monkeypatch.setattr(runtime, "skill_sources", lambda **_: iter(sources))
    monkeypatch.setattr(
        "deeptutor.services.workspace.resources.current_resources",
        lambda: SimpleNamespace(skills=selected),
    )
    monkeypatch.setattr("deeptutor.services.workspace.context.current_workspace_id", lambda: "")


def skill(tmp_path, name="demo", text="hello"):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Demo\n---\n{text}")
    return SkillService(root=tmp_path, builtin_root=None)


def test_builtin_is_complete_and_readonly_including_binary_assets(tmp_path, monkeypatch):
    service = SkillService(root=BUILTIN_SKILLS_ROOT, builtin_root=None)
    use_sources(monkeypatch, (service, None, "builtin"))
    source = BUILTIN_SKILLS_ROOT / "best-paper-review"
    provenance = json.loads((source / "PROVENANCE.json").read_text())
    assert len(provenance["upstream_files"]) == 247
    assert provenance["ref"] == "4f4e7d50af17290fc9cb908821db13098de09361"
    output = execution.stage_skill(tmp_path / "task", "best-paper-review")
    for name, expected in provenance["upstream_files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected
    assert "MIT License" in (output / "LICENSE").read_text()
    assert (
        output / "assets/templates/pdf_viewer/vendor/pdf.worker.min.js"
    ).stat().st_size > 1_000_000
    assert (output / "assets/templates/pdf_viewer/vendor/cmaps/Adobe-GB1-0.bcmap").is_file()
    for name in (
        "assets/templates/pdf_viewer/vendor/LICENSE.pdfjs",
        "assets/templates/pdf_viewer/vendor/LICENSE.pdf-lib.md",
        "assets/templates/pdf_viewer/vendor/cmaps/LICENSE",
        "assets/vendor/katex/LICENSE",
    ):
        assert (output / name).is_file()
    assert all(not p.stat().st_mode & 0o222 for p in [output, *output.rglob("*")])
    assert execution.stage_skill(tmp_path / "task", "best-paper-review") == output


def test_workspace_shadow_is_selected_and_revocation_is_rechecked(tmp_path, monkeypatch):
    workspace = skill(tmp_path / "workspace", text="workspace edition")
    builtin = skill(tmp_path / "builtin", text="builtin edition")
    use_sources(monkeypatch, (workspace, None, "workspace"), (builtin, None, "builtin"))
    task = tmp_path / "task"
    output = execution.stage_skill(task, "demo")
    assert "workspace edition" in (output / "SKILL.md").read_text()
    use_sources(monkeypatch, (workspace, None, "workspace"), selected=set())
    with pytest.raises(SkillNotFoundError):
        execution.stage_skill(task, "demo")
    use_sources(monkeypatch, (workspace, {"other"}, "admin"))
    with pytest.raises(SkillNotFoundError):
        execution.stage_skill(task, "demo")


@pytest.mark.parametrize("name", ["../demo", "/demo", "demo/child", ".", ""])
def test_invalid_names_are_rejected_before_resolution(tmp_path, monkeypatch, name):
    monkeypatch.setattr(
        execution, "resolve_runtime_skill", lambda _: pytest.fail("must not resolve")
    )
    with pytest.raises(InvalidSkillNameError):
        execution.stage_skill(tmp_path / "task", name)


@pytest.mark.parametrize("location", ["file", "directory", "root", "destination"])
def test_symbolic_links_are_not_followed(tmp_path, monkeypatch, location):
    service = skill(tmp_path / "source")
    use_sources(monkeypatch, (service, None, "account"))
    source = service.root / "demo"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("private")
    task = tmp_path / "task"
    if location == "file":
        (source / "reference.txt").symlink_to(outside / "secret")
    elif location == "directory":
        (source / "references").symlink_to(outside, target_is_directory=True)
    elif location == "root":
        source.rename(outside / "demo")
        source.symlink_to(outside / "demo", target_is_directory=True)
    else:
        task.mkdir()
        (task / ".deeptutor").symlink_to(outside, target_is_directory=True)
    with pytest.raises((execution.SkillExecutionError, OSError)):
        execution.stage_skill(task, "demo")
    assert not (outside / "skills").exists()


def test_partial_copy_is_never_published_and_can_be_retried(tmp_path, monkeypatch):
    service = skill(tmp_path / "source")
    use_sources(monkeypatch, (service, None, "account"))
    scan = execution._scan

    def interrupted(root, **kwargs):
        result = scan(root, **kwargs)
        if kwargs.get("copy_to") is not None:
            raise OSError("copy interrupted")
        return result

    monkeypatch.setattr(execution, "_scan", interrupted)
    task = tmp_path / "task"
    with pytest.raises(OSError, match="copy interrupted"):
        execution.stage_skill(task, "demo")
    assert list((task / ".deeptutor/skills").iterdir()) == []
    monkeypatch.setattr(execution, "_scan", scan)
    output = execution.stage_skill(task, "demo")
    assert (output / "SKILL.md").is_file()


def test_repeat_does_not_overwrite_a_changed_snapshot_or_source(tmp_path, monkeypatch):
    service = skill(tmp_path / "source")
    use_sources(monkeypatch, (service, None, "account"))
    task = tmp_path / "task"
    output = execution.stage_skill(task, "demo")
    original = (output / "SKILL.md").read_bytes()
    (service.root / "demo/SKILL.md").write_bytes(original + b"\nchanged")
    with pytest.raises(execution.SkillExecutionError):
        execution.stage_skill(task, "demo")
    assert (output / "SKILL.md").read_bytes() == original
    (service.root / "demo/SKILL.md").write_bytes(original)
    (output / "SKILL.md").chmod(0o644)
    (output / "SKILL.md").write_bytes(b"corrupted")
    (output / "SKILL.md").chmod(0o444)
    with pytest.raises(execution.SkillExecutionError):
        execution.stage_skill(task, "demo")


def test_concurrent_staging_publishes_one_verified_package(tmp_path, monkeypatch):
    service = skill(tmp_path / "source")
    use_sources(monkeypatch, (service, None, "account"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: execution.stage_skill(tmp_path / "task", "demo"), range(2))
        )
    assert results[0] == results[1]
    assert [p.name for p in results[0].parent.iterdir()] == ["demo"]
