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
