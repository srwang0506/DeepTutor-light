"""Opt-in OS sandbox acceptance; never compile the user's source for this test."""

import os
from pathlib import Path
import shutil

import pytest

from deeptutor.studio.database import Studio
from deeptutor.studio.manuscripts import compile_project, create_manuscript, read_file, save_file
from deeptutor.studio.models import FileEdit, ManuscriptInput

pytestmark = pytest.mark.skipif(
    os.getenv("STUDIO_TEX_INTEGRATION") != "1", reason="Opt-in sandbox integration"
)


def test_compile_retains_success_and_denies_outside_input(tmp_path):
    assert shutil.which("sandbox-exec") and shutil.which("latexmk")
    store = Studio(tmp_path / "store", "fixture")
    m = create_manuscript(store, ManuscriptInput(title="Sandbox fixture"))
    good = compile_project(store, m["id"])
    assert good["data"]["status"] == "succeeded"
    digest = good["data"]["outputs"]["preview.pdf"]
    assert store.blob_path(digest).read_bytes().startswith(b"%PDF-")
    outside = tmp_path / "outside.tex"
    outside.write_text("PRIVATE_FIXTURE_MARKER")
    current = read_file(store, m["id"], "main.tex")
    save_file(
        store,
        m["id"],
        FileEdit(
            path="main.tex",
            base_hash=current["hash"],
            text=r"\documentclass{article}\begin{document}\input{"
            + str(outside)
            + r"}\end{document}",
        ),
    )
    failed = compile_project(store, m["id"])
    assert failed["data"]["status"] == "failed"
    assert "preview.pdf" not in failed["data"]["outputs"]
    assert store.get(good["id"])["data"]["outputs"]["preview.pdf"] == digest
    assert outside.read_text() == "PRIVATE_FIXTURE_MARKER"
