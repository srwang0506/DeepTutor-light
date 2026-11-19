"""Crash and fencing regressions for the optional diskless hosted deployment."""

import asyncio
import base64
from io import BytesIO
import json
import os
import sqlite3
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")
from deployment.hosted.neon_store import CloudStore, FencedWriter
from deployment.hosted.writeback import CommitMiddleware


class Objects:
    def __init__(self):
        self.blocks = {}

    def get_object(self, *, Bucket, Key):
        return {"Body": BytesIO(self.blocks[Key])}

    def put_object(self, *, Bucket, Key, Body):
        self.blocks[Key] = Body

    def delete_object(self, *, Bucket, Key):
        del self.blocks[Key]

    def get_paginator(self, _operation):
        return self

    def paginate(self, *, Bucket, Prefix):
        yield {"Contents": [{"Key": key} for key in self.blocks if key.startswith(Prefix)]}


@pytest.fixture
def cloud():
    url = os.environ.get("DEEPTUTOR_TEST_POSTGRES")
    if not url:
        pytest.skip("Set DEEPTUTOR_TEST_POSTGRES to run the dedicated PostgreSQL integration test")
    settings = dict(
        database_url=url,
        bucket="test",
        namespace=uuid4().hex,
        encryption_key=base64.b64encode(os.urandom(32)).decode(),
    )
    storage = Objects()
    return lambda: CloudStore(settings, storage), storage


def test_replacement_recovers_acknowledged_wal_and_private_paper(tmp_path, cloud):
    new_store, objects = cloud
    first, replacement = new_store(), new_store()
    old, fresh = tmp_path / "old", tmp_path / "fresh"
    first.claim(old)
    data = old / "data"
    data.mkdir(parents=True)
    (data / "empty folder").mkdir()
    paper = data / "论文.pdf"
    paper.write_bytes(b"%PDF-1.4\nprivate manuscript")
    (data / "settings.json").write_text('{"language":"zh"}')
    db = sqlite3.connect(data / "chat.sqlite3")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE notes (body TEXT)")
        db.execute("INSERT INTO notes VALUES ('binary search note')")
        db.commit()
        first.save(old, acknowledge=True)
        assert all(b"private manuscript" not in block for block in objects.blocks.values())
        previous = first.revision
        first.save(old, acknowledge=True)
        assert first.revision == previous
        # No shutdown hook runs. The first SQLite connection is still open.
        replacement.claim(fresh)
        with sqlite3.connect(fresh / "data/chat.sqlite3") as restored:
            assert restored.execute("SELECT body FROM notes").fetchone() == ("binary search note",)
        assert (fresh / "data/论文.pdf").read_bytes() == paper.read_bytes()
        assert (fresh / "data/empty folder").is_dir()
        with pytest.raises(FencedWriter):
            first.save(old, acknowledge=True)
        (fresh / "data/论文.pdf").unlink()
        replacement.save(fresh, acknowledge=True)
        replacement.prune_files()
        assert not objects.blocks
    finally:
        db.close()


def test_corrupt_object_cannot_supersede_current_writer(tmp_path, cloud):
    new_store, objects = cloud
    first = new_store()
    home = tmp_path / "active"
    first.claim(home)
    data = home / "data"
    data.mkdir(parents=True)
    (data / "original.pdf").write_bytes(b"private")
    first.save(home, acknowledge=True)
    key, blob = next(iter(objects.blocks.items()))
    objects.blocks[key] = blob[:-1] + bytes([blob[-1] ^ 1])
    fresh = tmp_path / "replacement"
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        new_store().claim(fresh)
    assert not (fresh / "data").exists()
    objects.blocks[key] = blob
    first.save(home, acknowledge=True)


def test_batched_restore_keeps_file_order_and_rejects_a_missing_state_block(tmp_path, cloud):
    new_store, objects = cloud
    first = new_store()
    home = tmp_path / "active"
    first.claim(home)
    data = home / "data"
    data.mkdir(parents=True)
    for index in range(38):
        (data / f"note-{index:02}.json").write_text(json.dumps({"note": index}))
    (data / "large.pdf").write_bytes(
        b"a" * (1024 * 1024) + b"b" * (1024 * 1024) + b"a" * (1024 * 1024)
    )
    first.save(home, acknowledge=True)
    assert len(objects.blocks) == 2
    replacement = new_store()
    fresh = tmp_path / "restored"
    replacement.claim(fresh)
    assert (fresh / "data/large.pdf").read_bytes() == (data / "large.pdf").read_bytes()
    assert (fresh / "data/note-37.json").read_bytes() == (data / "note-37.json").read_bytes()
    replacement.check_reader()
    with pytest.raises(FencedWriter):
        first.check_reader()
    digest = first.manifest["files"]["note-17.json"]["pieces"][0]
    with replacement.connection() as db:
        db.execute(
            "DELETE FROM dt_cloud_blocks WHERE workspace=%s AND digest=%s", (first.name, digest)
        )
    failed = tmp_path / "failed"
    with pytest.raises(ValueError, match="block missing"):
        new_store().claim(failed)
    assert not (failed / "data").exists()
    replacement.check_reader()


def test_navigation_shares_only_reader_checks_and_writes_still_wait(monkeypatch):
    calls = []

    async def exercise():
        gate = asyncio.Event()

        async def confirm(_socket, mode):
            calls.append(mode)
            await gate.wait()

        monkeypatch.setattr("deployment.hosted.writeback.commit", confirm)

        async def app(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})

        middleware = CommitMiddleware(app, "unused")

        async def visit(method, path):
            async def send(event):
                assert event["status"] == 200

            await middleware({"type": "http", "path": path, "method": method}, None, send)

        burst = asyncio.gather(*(visit("GET", "/api/sessions") for _ in range(25)))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == [b"owner"] and not burst.done()
        gate.set()
        await burst
        await visit("GET", "/api/calendar/events")
        await visit("POST", "/api/calendar/events")
        await visit("GET", "/api/settings/workspace/registrations")
        middleware.reader_until = 0
        await visit("GET", "/api/sessions")
        assert calls == [b"owner", b"write", b"read", b"owner"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "scope,messages,expected",
    [
        (
            {"type": "http", "method": "POST", "path": "/api/notebooks"},
            [
                {"type": "http.response.start", "status": 200, "headers": []},
                {"type": "http.response.body", "body": b"saved"},
            ],
            "http.response.start",
        ),
        (
            {"type": "websocket", "path": "/ws"},
            [{"type": "websocket.send", "text": json.dumps({"type": "done"})}],
            "websocket.close",
        ),
    ],
)
def test_failed_commit_withholds_success(monkeypatch, scope, messages, expected):
    calls = []

    async def offline(_path, _mode):
        calls.append("attempt")
        raise OSError()

    async def app(_scope, _receive, send):
        for message in messages:
            await send(message)

    async def collect(message):
        calls.append(message)

    monkeypatch.setattr("deployment.hosted.writeback.commit", offline)
    asyncio.run(CommitMiddleware(app, "unused")(scope, None, collect))
    assert calls[0] == "attempt" and calls[1]["type"] == expected
    assert calls[1].get("status") == 503 or calls[1].get("code") == 1013
    assert not any(isinstance(msg, dict) and msg.get("body") == b"saved" for msg in calls)
