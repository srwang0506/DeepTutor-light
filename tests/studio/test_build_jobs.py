import json
import sys
import threading
import time

import pytest

from deeptutor.studio import builds, manuscripts
from deeptutor.studio.connections import ProcessCancelled, bounded_process
from deeptutor.studio.database import Conflict, Missing, Studio, encoded, sha
from deeptutor.studio.models import FileEdit, ManuscriptInput


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "studio", "project-a")


def manuscript(store):
    return manuscripts.create_manuscript(store, ManuscriptInput(title="Build fixture"))["id"]


def wait_status(store, identifier, states):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        row = store.get(identifier, "build")
        if row["data"]["status"] in states:
            return row
        time.sleep(0.01)
    raise AssertionError(store.get(identifier, "build"))


def test_queue_deduplicates_and_cancellation_stops_worker(store, monkeypatch):
    entered, released = threading.Event(), threading.Event()

    def execute(store, row, cancelled):
        entered.set()
        assert released.wait(3)
        assert cancelled()
        with store.db(write=True) as db:
            current = store.record(db, row["id"], "build")
            return store.put(
                db,
                "build",
                {**current["data"], "status": "cancelled"},
                identifier=row["id"],
                expected=current["revision"],
            )

    monkeypatch.setattr(manuscripts, "_execute_build", execute)
    mid = manuscript(store)
    try:
        first = builds.enqueue(store, mid)
        assert entered.wait(3)
        assert builds.enqueue(store, mid)["id"] == first["id"]
        builds.resume(store)
        assert store.get(first["id"])["data"]["status"] == "running"
        result = builds.cancel(store, first["id"])
        assert result["data"]["cancel_requested"]
        with pytest.raises(Missing):
            builds.cancel(Studio(store.root, "project-b"), first["id"])
    finally:
        released.set()
    wait_status(store, first["id"], {"cancelled"})


def test_crash_marks_interrupted_retry_uses_frozen_inputs_including_empty(store, monkeypatch):
    mid = manuscript(store)
    manuscripts.save_file(store, mid, FileEdit(path="empty.tex", base_hash=sha(b""), text=""))
    row = manuscripts.prepare_build(store, mid)
    assert row["data"]["input_files"]["empty.tex"] == sha(b"")
    with store.db(write=True) as db:
        row = store.put(
            db,
            "build",
            {**row["data"], "status": "running"},
            identifier=row["id"],
            expected=row["revision"],
        )
    builds.resume(store)
    assert store.get(row["id"])["data"]["status"] == "interrupted"
    file = manuscripts.read_file(store, mid, "main.tex")
    manuscripts.save_file(
        store, mid, FileEdit(path="main.tex", base_hash=file["hash"], text="new text")
    )
    launched = []
    monkeypatch.setattr(builds, "launch", lambda store, identifier: launched.append(identifier))
    retry = builds.retry(store, row["id"])
    assert retry["data"]["input_files"] == row["data"]["input_files"]
    assert retry["data"]["retry_of"] == row["id"]
    assert retry["id"] != row["id"]
    assert launched == [retry["id"]]
    assert builds.retry(store, row["id"])["id"] == retry["id"]
    assert len(store.list("build")) == 2


def test_cancel_process_group_is_bounded():
    start = time.monotonic()
    with pytest.raises(ProcessCancelled):
        bounded_process(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            timeout=30,
            cancelled=lambda: True,
        )
    assert time.monotonic() - start < 2


@pytest.mark.parametrize(
    "choice,text", [("disk", "external edit"), ("before", "before"), ("prepared", "prepared")]
)
def test_recovery_resolution_keeps_all_three_versions_and_checks_disk(store, choice, text):
    mid = manuscript(store)
    root = manuscripts.tree(store, mid)
    before, after = store.save_blob(b"before"), store.save_blob(b"prepared")
    journal = root / ".studio-save-abcdef.json"
    journal.write_text(encoded({"path": "main.tex", "before": before, "after": after}))
    (root / "main.tex").write_text("external edit")
    conflict = manuscripts.recover_saves(store, mid)[0]
    assert conflict["before_text"] == "before"
    with pytest.raises(Conflict):
        manuscripts.resolve_recovery(store, mid, journal.name, sha(b"old disk"), choice)
    manuscripts.resolve_recovery(store, mid, journal.name, conflict["disk"], choice)
    assert (root / "main.tex").read_text() == text
    assert store.blob_path(sha(b"external edit")).read_text() == "external edit"
    assert store.blob_path(before).read_text() == "before"
    assert store.blob_path(after).read_text() == "prepared"
    assert not journal.exists()
    assert manuscripts.recover_saves(store, mid) == []
    assert store.list("recovery")[0]["data"]["choice"] == choice


def test_invalid_save_journal_is_visible_and_archived_without_rewriting(store):
    mid = manuscript(store)
    original = manuscripts.read_file(store, mid, "main.tex")
    journal = manuscripts.tree(store, mid) / ".studio-save-abcdef.json"
    journal.write_text("{truncated")
    assert manuscripts.recover_saves(store, mid)[0]["invalid"]
    with pytest.raises(Conflict):
        manuscripts.save_file(
            store, mid, FileEdit(path="main.tex", base_hash=original["hash"], text="overwrite")
        )
    manuscripts.resolve_recovery(store, mid, journal.name, "", "disk")
    assert manuscripts.read_file(store, mid, "main.tex") == original
    assert (
        store.blob_path(store.list("recovery")[0]["data"]["journal_hash"]).read_text()
        == "{truncated"
    )
