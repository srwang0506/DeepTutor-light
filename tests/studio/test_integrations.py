import asyncio
import json

import httpx
import pytest

from deeptutor.studio import integrations as i
from deeptutor.studio.database import Conflict, Studio


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "studio", project="course")


def source(store, **kwargs):
    return i.save(
        store,
        i.SourceInput(
            title="Library", provider="zotero", library_id="123", api_key="private-key", **kwargs
        ),
    )


def remote(version=7, changed=True, deleted=None, mutate=None):
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.zotero.org"
        assert request.headers["Zotero-API-Key"] == "private-key"
        path = request.url.path
        headers = {"Last-Modified-Version": str(version)}
        if path == "/keys/current":
            return httpx.Response(200, json={"userID": 123, "access": {"user": {"library": True}}})
        if path.endswith("/deleted"):
            if mutate:
                mutate()
            return httpx.Response(200, headers=headers, json={"items": deleted or []})
        if request.url.params.get("format") == "versions":
            return httpx.Response(
                200, headers=headers, json={"ABCD1234": version} if changed else {}
            )
        return httpx.Response(
            200,
            headers=headers,
            json=[
                {
                    "key": "ABCD1234",
                    "version": version,
                    "data": {
                        "itemType": "conferencePaper",
                        "title": "A paper",
                        "DOI": "10.1234/paper",
                        "pages": "12-20",
                        "creators": [],
                    },
                }
            ],
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_zotero_incremental_tombstone_and_encryption(store):
    s = source(store)
    assert "private-key" not in json.dumps(s)
    with store.db() as db:
        encrypted = db.execute("SELECT encrypted FROM integration_secrets").fetchone()[0]
        assert b"private-key" not in encrypted
    current = await i.refresh(store, s["id"], transport=remote())
    assert current["data"]["cursor"] == {"version": 7}
    first = i.source_rows(store, s["id"])[0]
    assert first["record_id"] and not first["deleted"]
    await i.refresh(store, s["id"], transport=remote(8, changed=False, deleted=["ABCD1234"]))
    assert i.source_rows(store, s["id"])[0]["deleted"] == 1
    assert store.get(first["record_id"])["kind"] == "edition"
    assert len(store.list("edition")) == 1


@pytest.mark.asyncio
async def test_zotero_revision_and_permission_fence(store):
    s = source(store)

    def revoke():
        i.save(
            store,
            i.SourceInput(title="Library", provider="zotero", library_id="123", enabled=False),
            s["id"],
            s["revision"],
        )

    with pytest.raises(Conflict):
        await i.refresh(store, s["id"], transport=remote(mutate=revoke))
    assert not store.list("edition")
    assert i.source_rows(store, s["id"]) == []
    with store.db() as db:
        assert not db.execute("SELECT * FROM integration_secrets").fetchall()


@pytest.mark.asyncio
async def test_zotero_retry_has_no_partial_cursor_or_import(store):
    s = source(store)
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if request.url.path.endswith("/deleted"):
            return httpx.Response(429, headers={"Retry-After": "120"})
        return remote().handler(request)

    with pytest.raises(i.RetryLater):
        await i.refresh(store, s["id"], transport=httpx.MockTransport(handler))
    assert store.get(s["id"])["data"]["cursor"] == {}
    assert store.get(s["id"])["data"]["state"] == "retrying"
    assert not store.list("edition")


@pytest.mark.asyncio
async def test_log_partial_lines_replay_rotation_and_author_edits(store, tmp_path):
    path = tmp_path / "events.jsonl"
    event = {
        "event_id": "one",
        "run_id": "r1",
        "type": "metric",
        "name": "loss",
        "value": 2.0,
        "step": 0,
    }
    path.write_text(json.dumps(event))
    s = i.save(store, i.SourceInput(title="Training", provider="local_log", path=str(path)))
    await i.refresh(store, s["id"])
    assert not store.list("run")
    with path.open("a") as f:
        f.write("\n")
    await i.refresh(store, s["id"])
    run = store.list("run")[0]
    with store.db(write=True) as db:
        store.put(
            db,
            "run",
            {**run["data"], "title": "My edited title"},
            identifier=run["id"],
            expected=run["revision"],
        )
    await i.refresh(store, s["id"])
    path.rename(tmp_path / "old.jsonl")
    path.write_text(
        json.dumps(event)
        + "\n"
        + json.dumps({**event, "event_id": "two", "value": 1.0, "step": 1})
        + "\n"
    )
    await i.refresh(store, s["id"])
    assert len(store.list("run")) == 1
    assert store.get(run["id"])["data"]["title"] == "My edited title"
    assert store.get(run["id"])["data"]["execution_verified"] is False
    assert [e["value"] for e in i.run_events(store, run["id"])["events"]] == [2.0, 1.0]


@pytest.mark.asyncio
async def test_log_changed_identity_is_rejected_atomically(store, tmp_path):
    path = tmp_path / "events.jsonl"
    event = {
        "event_id": "one",
        "run_id": "r1",
        "type": "metric",
        "name": "loss",
        "value": 2.0,
        "step": 0,
    }
    path.write_text(json.dumps(event) + "\n")
    s = i.save(store, i.SourceInput(title="Training", provider="local_log", path=str(path)))
    await i.refresh(store, s["id"])
    before = store.get(s["id"])["data"]["cursor"]
    with path.open("a") as f:
        f.write(json.dumps({**event, "value": 100}) + "\n")
    with pytest.raises(Conflict):
        await i.refresh(store, s["id"])
    assert store.get(s["id"])["data"]["cursor"] == before
    assert len(i.run_events(store, store.list("run")[0]["id"])["events"]) == 1


def test_event_windows_do_not_lose_pagination_boundaries(tmp_path):
    import json

    from deeptutor.studio import integrations as module
    from deeptutor.studio.database import Studio

    store = Studio(tmp_path / "events", project="p")
    with store.db(write=True) as db:
        module.schema(db)
        run = store.put(db, "run", {"title": "Large experiment"})
        for i in range(25):
            payload = {
                "event_id": str(i),
                "type": "metric",
                "name": "loss",
                "step": i,
                "value": 1 / (i + 1),
            }
            db.execute(
                "INSERT INTO integration_events VALUES (?,?,?,?,?,?)",
                ("s", str(i), str(i), run["id"], json.dumps(payload), i),
            )
    recent = module.run_events(store, run["id"], "latest", 10)
    assert [e["step"] for e in recent["events"]] == list(range(15, 25))
    older = module.run_events(store, run["id"], "latest", 10, int(recent["first_cursor"]))
    assert [e["step"] for e in older["events"]] == list(range(5, 15))
    assert module.run_events(store, run["id"], recent["cursor"])["events"] == []
