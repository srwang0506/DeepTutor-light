"""Restore the complete personal workflow to a genuinely different runtime path."""

import json

from deeptutor.co_writer.storage import CoWriterStorage
from deeptutor.notes.review import due_cards, grade_card, page_cards
from deeptutor.runtime.backup import backup, restore
from deeptutor.studio import catalog, manuscripts
from deeptutor.studio.database import Studio
from deeptutor.studio.models import FileEdit, ManuscriptInput
from tests.notes.test_anki_export import _flash_block
from tests.notes.test_block_pages import Paths
from tests.studio.test_domains import original_evidence


def test_native_backup_restores_pdf_anchor_card_schedule_and_uncommitted_manuscript(tmp_path):
    home = tmp_path / "original-home"
    project = "permanent-project-uuid"
    studio = Studio(home / "data" / "user" / "studio", project)
    source = original_evidence(studio)
    manuscript = manuscripts.create_manuscript(studio, ManuscriptInput(title="Uncommitted fixture"))
    original = manuscripts.read_file(studio, manuscript["id"], "main.tex")
    text = original["text"].replace(
        "Write your evidence-grounded argument here.",
        "An uncommitted sentence survives relocation.",
    )
    manuscripts.save_file(
        studio, manuscript["id"], FileEdit(path="main.tex", base_hash=original["hash"], text=text)
    )
    notes_path = home / "data" / "user" / project / "notes"
    notes = CoWriterStorage(Paths(notes_path))
    doc = notes.create_document(
        title="DSA fixture",
        blocks=[_flash_block("flash", [{"front": "BFS queue?", "back": "FIFO"}])],
    )
    card = next(page_cards(doc))
    grade_card(notes, doc.id, card["card_id"], True, card["content_hash"])
    history = json.loads((notes.doc_root(doc.id) / "review.json").read_text())
    snapshot = tmp_path / "native-backup"
    backup(home, snapshot)
    # Change the original afterwards to prove restore uses the immutable backup.
    source_path = manuscripts.project_file(studio, manuscript["id"], "main.tex")
    source_path.write_text("AFTER BACKUP")
    relocated = tmp_path / "different-directory" / "restored-home"
    restore(snapshot, relocated)
    restored = Studio(relocated / "data" / "user" / "studio", project)
    restored_evidence = restored.get(source["id"], "evidence")["data"]
    page = catalog.page_source(
        restored,
        restored_evidence["edition_id"],
        restored_evidence["blob_hash"],
        restored_evidence["page"],
    )
    assert restored_evidence["quote"] in page["text"]
    assert manuscripts.read_file(restored, manuscript["id"], "main.tex")["text"] == text
    assert (manuscripts.tree(restored, manuscript["id"]) / ".git").is_dir()
    restored_notes = CoWriterStorage(Paths(relocated / "data" / "user" / project / "notes"))
    restored_card = next(page_cards(restored_notes.load_document(doc.id)))
    assert restored_card["card_id"] == card["card_id"]
    assert json.loads((restored_notes.doc_root(doc.id) / "review.json").read_text()) == history
    assert not due_cards(restored_notes)
