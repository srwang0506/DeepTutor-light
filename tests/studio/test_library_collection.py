from concurrent.futures import ThreadPoolExecutor

import fitz
import pytest

from deeptutor.studio import catalog
from deeptutor.studio.database import Conflict, Missing, Studio
from deeptutor.studio.models import EditionInput, LibraryCollectionInput, WorkInput


def command(**kwargs):
    return LibraryCollectionInput(
        request_id="collection-request-1",
        work=WorkInput(title="A paper without an identifier"),
        edition=EditionInput(work_id="", **kwargs),
    )


def test_collection_replay_and_concurrent_retry_do_not_duplicate(tmp_path):
    store = Studio(tmp_path / "account", project="study")
    payload = command()
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: catalog.collect_library(store, payload), range(3)))
    assert len({row["id"] for row in rows}) == 1
    assert len(store.list("work")) == len(store.list("edition")) == 1
    assert (
        catalog.library_page(store, project_only=True)["works"][0]["editions"][0]["project_role"]
        == "reading"
    )
    changed = payload.model_copy(update={"work": WorkInput(title="Different content")})
    with pytest.raises(Conflict):
        catalog.collect_library(store, changed)
    assert len(store.list("work")) == 1
    assert Studio(tmp_path / "other-account", project="study").list("work") == []


def test_invalid_or_duplicate_edition_does_not_leave_orphan_work(tmp_path):
    store = Studio(tmp_path / "account", project="study")
    with pytest.raises(ValueError):
        catalog.collect_library(store, command(doi="invalid"))
    assert store.list("work") == []
    first = command(doi="10.1234/example")
    catalog.collect_library(store, first)
    second = first.model_copy(update={"request_id": "collection-request-2"})
    with pytest.raises(Conflict):
        catalog.collect_library(store, second)
    assert len(store.list("work")) == len(store.list("edition")) == 1


def test_lost_pdf_response_can_be_retried_without_replacing_other_changes(tmp_path, monkeypatch):
    store = Studio(tmp_path / "account", project="study")
    row = catalog.collect_library(store, command())
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Original source")
        original = document.tobytes()
    attached = catalog.attach_pdf(store, row["id"], original, "paper.pdf", row["revision"])
    replay = catalog.attach_pdf(store, row["id"], original, "renamed.pdf", row["revision"])
    assert replay["revision"] == attached["revision"]
    assert len(replay["data"]["files"]) == 1
    assert catalog.collect_library(store, command())["revision"] == attached["revision"]
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Different original")
        other = document.tobytes()
    writes = []
    monkeypatch.setattr(store, "save_blob", lambda data: writes.append(data))
    with pytest.raises(Conflict):
        catalog.attach_pdf(store, row["id"], other, "other.pdf", row["revision"])
    with pytest.raises(Missing):
        catalog.attach_pdf(store, "missing-edition", other, "other.pdf", 1)
    assert writes == []


def test_collect_another_edition_uses_the_existing_work(tmp_path):
    store = Studio(tmp_path / "account", project="study")
    first = catalog.collect_library(store, command())
    second = catalog.collect_library(
        store,
        LibraryCollectionInput(
            request_id="collection-request-2",
            edition=EditionInput(work_id=first["data"]["work_id"], label="Published"),
        ),
    )
    assert second["id"] != first["id"]
    assert len(store.list("work")) == 1
    assert len(store.list("edition")) == 2


def test_pdf_replay_restores_missing_bytes_and_checks_existing_integrity(tmp_path):
    store = Studio(tmp_path / "account", project="study")
    row = catalog.collect_library(store, command())
    with fitz.open() as document:
        document.new_page()
        original = document.tobytes()
    attached = catalog.attach_pdf(store, row["id"], original, "paper.pdf", row["revision"])
    path = store.blob_path(attached["data"]["files"][0]["hash"])
    path.unlink()
    replay = catalog.attach_pdf(store, row["id"], original, "paper.pdf", row["revision"])
    assert replay["revision"] == attached["revision"]
    assert path.read_bytes() == original
    path.write_bytes(b"corrupt fixture")
    with pytest.raises(Conflict, match="integrity"):
        catalog.attach_pdf(store, row["id"], original, "paper.pdf", row["revision"])
