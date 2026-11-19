import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from deeptutor.studio import assist, writing_skill
from deeptutor.studio import manuscripts as ms
from deeptutor.studio import personal_editor as editor
from deeptutor.studio.database import Conflict, Missing, Studio, sha
from deeptutor.studio.models import FileEdit, ManuscriptInput
from deeptutor.studio.review_markup import printable


@pytest.fixture
def paper(tmp_path):
    store = Studio(tmp_path / "studio", "one")
    mid = ms.create_manuscript(store, ManuscriptInput(title="HOI manuscript"))["id"]
    editor.upload(store, mid, "sections/method.tex", b"A pilot observation.\n")
    editor.upload(store, mid, "notes.md", b"A pilot note.\n")
    return store, mid


def change(store, mid, name, new):
    original = ms.read_file(store, mid, name)
    return ms.save_file(store, mid, FileEdit(path=name, base_hash=original["hash"], text=new))


def test_multifile_replace_preview_conflict_and_all_or_nothing(paper):
    store, mid = paper
    spec = editor.Search(query=r"p(il)ot", replacement=r"p\1ot-scale", regexp=True)
    preview = editor.search(store, mid, spec)
    assert (preview["total"], preview["files"]) == (2, 2)
    assert editor.search(store, mid, editor.Search(query="PILOT"))["total"] == 2
    change(store, mid, "notes.md", "A pilot note changed externally.\n")
    with pytest.raises(Conflict):
        editor.search(
            store, mid, spec.model_copy(update={"preview_id": preview["preview_id"]}), apply=True
        )
    assert ms.read_file(store, mid, "sections/method.tex")["text"] == "A pilot observation.\n"
    preview = editor.search(store, mid, spec)
    editor.search(
        store, mid, spec.model_copy(update={"preview_id": preview["preview_id"]}), apply=True
    )
    assert "pilot-scale" in ms.read_file(store, mid, "notes.md")["text"]
    with pytest.raises(Missing):
        editor.project_index(Studio(store.root, "other"), mid)


def test_recovery_move_trash_and_live_archive(paper, monkeypatch):
    store, mid = paper
    digest = ms.read_file(store, mid, "notes.md")["hash"]
    actual = editor.recover_batch

    def interrupt(s, m):
        if (ms.tree(s, m).parent / "pending-editor-edit.json").exists():
            raise InterruptedError()
        actual(s, m)

    with monkeypatch.context() as ctx:
        ctx.setattr(editor, "recover_batch", interrupt)
        with pytest.raises(InterruptedError):
            editor.manage_file(store, mid, "notes.md", digest, "notes/draft.md")
    assert ms.read_file(store, mid, "notes/draft.md")["hash"] == digest
    editor.manage_file(store, mid, "notes/draft.md", digest)
    item = editor.trash(store, mid)[0]
    editor.upload(store, mid, "notes/draft.md", b"")
    with pytest.raises(Conflict):
        editor.restore_trash(store, mid, item["id"])
    editor.manage_file(store, mid, "notes/draft.md", sha(b""))
    editor.restore_trash(store, mid, item["id"])
    with zipfile.ZipFile(io.BytesIO(editor.archive(store, mid))) as archive:
        assert archive.read("notes/draft.md") == b"A pilot note.\n"
        assert all(".git" not in name for name in archive.namelist())
    with pytest.raises(ValueError):
        editor.upload(store, mid, "../outside.tex", b"x")


def test_git_roundtrip_restores_without_losing_browser_edits(paper, tmp_path):
    store, mid = paper
    before = editor.checkpoint(store, mid, "First draft")["commit"]
    source = tmp_path / "paper.bundle"
    source.write_bytes(editor.git_bundle(store, mid))
    clone = tmp_path / "paper"
    subprocess.run(["git", "clone", "-q", str(source), str(clone)], check=True)
    (clone / "sections/method.tex").write_text("An offline edit.\n")

    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.email=author@localhost",
                "-c",
                "user.name=Author",
                "-C",
                str(clone),
                *args,
            ]
        )

    git("commit", "-qam", "Revise method")
    patch = tmp_path / "changes.bundle"
    git("bundle", "create", str(patch), "HEAD")
    change(store, mid, "notes.md", "An independent browser edit.\n")
    incoming = editor.import_bundle(store, mid, patch.read_bytes())
    assert not incoming["conflicts"] and incoming["files"] == ["sections/method.tex"]
    change(store, mid, "sections/method.tex", "A competing edit.\n")
    with pytest.raises(Conflict):
        editor.import_bundle(store, mid, patch.read_bytes(), incoming["preview_id"])
    assert editor.import_bundle(store, mid, patch.read_bytes())["conflicts"] == [
        "sections/method.tex"
    ]
    change(store, mid, "sections/method.tex", "A pilot observation.\n")
    incoming = editor.import_bundle(store, mid, patch.read_bytes())
    editor.import_bundle(store, mid, patch.read_bytes(), incoming["preview_id"])
    assert ms.read_file(store, mid, "notes.md")["text"].startswith("An independent")
    assert ms.read_file(store, mid, "sections/method.tex")["text"].startswith("An offline")
    assert len(editor.history(store, mid)) >= 3
    assert editor.blame(store, mid, "sections/method.tex")["lines"][0]["title"] == "Revise method"
    diff = editor.version(store, mid, before)
    editor.version(store, mid, before, restore_id=diff["restore_id"])
    assert ms.read_file(store, mid, "notes.md")["text"] == "A pilot note.\n"


def test_reader_projection_removes_notes_and_preserves_source_lines():
    text = (
        r"\section{Method}"
        + "\n"
        + r"\sectiontldr{private idea}"
        + "\n"
        + (
            r"\cmtbg{id}{Author}visible\cmted{private \cmtrpl{1}{Author}{reply}}"
            r"\delbg{old}{Author}deleted\deled{}" + "\n% latexcoder:thread:private\n"
        )
    )
    result = printable("main.tex", text.encode()).decode()
    assert "visible" in result and "private" not in result and "deleted" not in result
    assert result.count("\n") == text.count("\n")
    original = rb"\newcommand{\tldr}[1]{\emph{#1}}\tldr{author's typeset note}"
    assert printable("main.tex", original) == original


@pytest.mark.asyncio
async def test_chapter_skill_records_report_without_inserting_notes(paper, monkeypatch):
    store, mid = paper
    monkeypatch.setattr(
        writing_skill, "_source", lambda name: writing_skill.BUILTIN_SKILLS_ROOT / name
    )
    guide = writing_skill.guide("method")
    assert (
        "references/method.md" in guide["files"]
        and "references/introduction.md" not in guide["files"]
    )
    name = "sections/method.tex"
    source = ms.read_file(store, mid, name)
    pref = assist.WritingPreferences(allow_cloud=True)
    assist.save_preferences(store, pref, 0)
    config = SimpleNamespace(provider_mode="oauth", provider_name="codex", model="test")
    cmd = assist.AssistInput(
        path=name,
        base_hash=source["hash"],
        start=0,
        end=0,
        section="method",
        language="zh",
        instruction="Draft a motivation.",
    )

    async def complete(prompt, system_prompt, max_tokens):
        assert json.loads(prompt)["selected_tex"] == "" and guide["instructions"] in system_prompt
        return json.dumps(
            {
                "replacement": "Motivation. ",
                "outline": ["Goal"],
                "paragraph_roles": ["Motivation"],
                "checks": ["No effect size provided."],
                "claims": [{"claim": "Needs evidence", "source_ids": ["invented"]}],
            }
        )

    row = await assist.propose(store, mid, cmd, config, complete)
    assert (
        row["revision"] == 1
        and row["data"]["generation"]["report"]["claims"][0]["source_ids"] == []
    )
    assert ms.read_file(store, mid, name) == source
    ms.accept_proposal(store, row["id"], row["revision"], True)
    assert ms.read_file(store, mid, name)["text"] == "Motivation. A pilot observation.\n"
    with pytest.raises(ValueError):
        writing_skill.parse_draft('{"replacement":"unexpected"}', [])
