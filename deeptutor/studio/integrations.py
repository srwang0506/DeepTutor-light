"""Durable, opt-in read-only Zotero and append-only local experiment sources.

Network/filesystem reads finish before the commit transaction. A revoked or
edited source cannot publish bytes. Cursors advance only with their events.
"""

import asyncio
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Literal

from cryptography.fernet import Fernet
from filelock import FileLock, Timeout
import httpx
from pydantic import Field, model_validator

from . import catalog
from .database import Conflict, Studio, encoded, sha
from .models import Command, RunInput

MAX_BATCH = 20_000_000


class SourceInput(Command):
    title: str = Field(min_length=1, max_length=200)
    provider: Literal["zotero", "local_log"]
    library_type: Literal["users", "groups"] = "users"
    library_id: str = Field(default="", max_length=30)
    path: str = Field(default="", max_length=4000)
    api_key: str = Field(default="", max_length=200, repr=False)
    enabled: bool = True
    interval_seconds: int = Field(default=300, ge=10, le=86400)

    @model_validator(mode="after")
    def valid_source(self):
        if self.provider == "zotero" and not re.fullmatch(r"[1-9][0-9]*", self.library_id):
            raise ValueError("Enter the Zotero user or group library ID.")
        if self.provider == "local_log":
            path = Path(self.path)
            if (
                not path.is_absolute()
                or ".." in path.parts
                or path.suffix not in {".jsonl", ".ndjson"}
            ):
                raise ValueError("Select one absolute JSONL experiment event file.")
        return self


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS integration_secrets (source TEXT PRIMARY KEY, encrypted BLOB NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS integration_objects (source TEXT NOT NULL, key TEXT NOT NULL, version INTEGER NOT NULL, record_id TEXT, deleted INTEGER NOT NULL DEFAULT 0, original TEXT NOT NULL, PRIMARY KEY(source,key))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS integration_events (source TEXT NOT NULL, event_id TEXT NOT NULL, digest TEXT NOT NULL, run_id TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(source,event_id))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS integration_run_events ON integration_events(source,run_id,created)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS integration_event_run ON integration_events(run_id)")


def cipher(store):
    path = store.root / ".integration-key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def save(store, command, identifier=None, expected=None):
    secret = cipher(store).encrypt(command.api_key.encode()) if command.api_key else None
    with store.db(write=True) as db:
        schema(db)
        old = store.record(db, identifier, "integration") if identifier else None
        data = old["data"] if old else {}
        values = command.model_dump(exclude={"api_key"})
        if old and any(
            data.get(k) != values[k] for k in ("provider", "library_type", "library_id", "path")
        ):
            raise Conflict(
                "Create a separate source when changing its identity; existing cursors belong to this source."
            )
        if not old and command.provider == "zotero" and not secret:
            raise ValueError("A Zotero read-only API key is required.")
        row = store.put(
            db,
            "integration",
            {
                **data,
                **values,
                "cursor": data.get("cursor", {}),
                "next_check": 0,
                "state": "ready" if command.enabled else "paused",
            },
            identifier=identifier,
            expected=expected,
        )
        if secret:
            db.execute(
                "INSERT OR REPLACE INTO integration_secrets VALUES (?,?)", (row["id"], secret)
            )
        if not command.enabled:
            db.execute("DELETE FROM integration_secrets WHERE source=?", (row["id"],))
        return row


def list_sources(store):
    return store.list("integration", limit=500)


def source_rows(store, identifier, limit=100, after=""):
    with store.db() as db:
        schema(db)
        store.record(db, identifier, "integration")
        return [
            dict(r) | {"original": json.loads(r["original"])}
            for r in db.execute(
                "SELECT * FROM integration_objects WHERE source=? AND key>? ORDER BY key LIMIT ?",
                (identifier, after, limit),
            )
        ]


class RetryLater(ValueError):
    def __init__(self, message, delay=60, state="retrying"):
        super().__init__(message)
        self.delay, self.state = delay, state


async def zotero_snapshot(data, key, transport=None):
    """Stable library snapshot; a moving version aborts without advancing cursors."""
    prefix = f"/{data['library_type']}/{data['library_id']}"
    since = int(data.get("cursor", {}).get("version", 0))
    headers = {"Zotero-API-Key": key, "Zotero-API-Version": "3"}
    cooldown = 0
    next_request = 0.0
    async with httpx.AsyncClient(
        base_url="https://api.zotero.org",
        headers=headers,
        timeout=25,
        follow_redirects=False,
        transport=transport,
    ) as client:

        async def get(path, params=None):
            nonlocal cooldown, next_request
            delay = next_request - time.monotonic()
            if delay > 60:
                raise RetryLater("Zotero requested a longer cooldown.", int(delay) + 1)
            if delay > 0:
                await asyncio.sleep(delay)
            async with client.stream("GET", path, params=params) as response:
                if response.status_code in {401, 403}:
                    raise RetryLater(
                        "Zotero authorization was revoked. Reconnect this library.",
                        state="unauthorized",
                        delay=86400,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raw = response.headers.get("Retry-After", "60")
                    raise RetryLater(
                        "Zotero asked this client to retry later.",
                        min(86400, max(1, int(raw) if raw.isdigit() else 60)),
                    )
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError("Unexpected Zotero redirect.")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_BATCH:
                        raise ValueError("Zotero response exceeds the bounded batch size.")
                raw_backoff = response.headers.get("Backoff", "0")
                response_delay = int(raw_backoff) if raw_backoff.isdigit() else 0
                cooldown = max(cooldown, response_delay)
                next_request = time.monotonic() + response_delay
                version = response.headers.get("Last-Modified-Version")
                return json.loads(chunks), int(version) if version else None

        access, _ = await get("/keys/current")
        if data["library_type"] == "users":
            allowed = str(access.get("userID")) == data["library_id"] and access.get(
                "access", {}
            ).get("user", {}).get("library")
        else:
            groups = access.get("access", {}).get("groups", {})
            allowed = groups.get(data["library_id"], groups.get("all", {})).get("library")
        if not allowed:
            raise RetryLater(
                "This key does not grant read access to the selected library.",
                state="unauthorized",
                delay=86400,
            )
        versions, version = await get(
            prefix + "/items", {"since": since, "format": "versions", "includeTrashed": 1}
        )
        if (
            version is None
            or version < since
            or not isinstance(versions, dict)
            or len(versions) > 10000
        ):
            raise ValueError("Invalid or oversized Zotero version inventory.")
        items = []
        keys = sorted(versions)
        if any(not re.fullmatch(r"[A-Z0-9]{8}", k) for k in keys):
            raise ValueError("Invalid Zotero object key.")
        for offset in range(0, len(keys), 50):
            batch, seen = await get(
                prefix + "/items",
                {
                    "itemKey": ",".join(keys[offset : offset + 50]),
                    "includeTrashed": 1,
                    "limit": 100,
                },
            )
            if seen != version:
                raise RetryLater("Library changed during download; retry from the saved cursor.", 2)
            if not isinstance(batch, list) or {r["key"] for r in batch} != set(
                keys[offset : offset + 50]
            ):
                raise ValueError("Incomplete Zotero batch; cursor was not advanced.")
            if any(r.get("version") != versions[r["key"]] for r in batch):
                raise RetryLater("Item version changed during download.", 2)
            items.extend(batch)
        deleted, seen = await get(prefix + "/deleted", {"since": since})
        if seen != version or not isinstance(deleted.get("items"), list):
            raise RetryLater("Deletion snapshot changed; retry from the saved cursor.", 2)
        return {
            "items": items,
            "deleted": deleted["items"],
            "version": version,
            "cooldown": cooldown,
        }


def zotero_csl(item):
    data = item["data"]
    kinds = {
        "conferencePaper": "paper-conference",
        "book": "book",
        "bookSection": "chapter",
        "thesis": "thesis",
        "report": "report",
    }
    authors = [
        {"literal": x["name"]}
        if x.get("name")
        else {"family": x.get("lastName", ""), "given": x.get("firstName", "")}
        for x in data.get("creators", [])
        if x.get("creatorType") in {"author", "inventor", "programmer"}
    ]
    year = re.search(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b", data.get("date", ""))
    return {
        "id": "zotero_" + item["key"],
        "type": kinds.get(data.get("itemType"), "article-journal"),
        "title": data.get("title") or "Untitled Zotero item",
        "author": authors,
        "DOI": data.get("DOI", ""),
        "URL": data.get("url", ""),
        "container-title": data.get("publicationTitle") or data.get("proceedingsTitle", ""),
        "page": data.get("pages", ""),
        "volume": data.get("volume", ""),
        "issue": data.get("issue", ""),
        "publisher": data.get("publisher", ""),
        "issued": {"date-parts": [[int(year[0])]]} if year else {},
    }


def commit_zotero(store, db, source, result):
    count = 0
    for item in result["items"]:
        key = item["key"]
        old = db.execute(
            "SELECT * FROM integration_objects WHERE source=? AND key=?", (source["id"], key)
        ).fetchone()
        record_id = old["record_id"] if old else None
        if item["data"].get("itemType") not in {"attachment", "note", "annotation"} and not item[
            "data"
        ].get("deleted"):
            imported = catalog.import_bibliography(
                store,
                json.dumps([zotero_csl(item)]),
                "csl",
                connection=db,
                edition_id=record_id,
                strict=True,
            )
            record_id = imported["entries"][0]["edition_id"]
            count += 1
        db.execute(
            "INSERT OR REPLACE INTO integration_objects VALUES (?,?,?,?,?,?)",
            (
                source["id"],
                key,
                item["version"],
                record_id,
                int(bool(item["data"].get("deleted"))),
                encoded(item),
            ),
        )
    for key in result["deleted"]:
        db.execute(
            "UPDATE integration_objects SET deleted=1,version=? WHERE source=? AND key=?",
            (result["version"], source["id"], key),
        )
    return {"version": result["version"]}, count


def read_log(data):
    path = Path(data["path"])
    # The same descriptor walk as SSH rejects symlinks in every component.
    cursor = data.get("cursor", {})
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Experiment source must be a regular file.")
        identity = f"{before.st_dev}:{before.st_ino}"
        offset = int(cursor.get("offset", 0)) if cursor.get("identity") == identity else 0
        if before.st_size < offset:
            offset = 0
        # Detect replacement/truncation to the same inode, including same-size rewrites.
        prefix = f.read(min(offset, 4096))
        if offset and sha(prefix) != cursor.get("prefix_hash"):
            offset = 0
        if offset and cursor.get("tail_hash"):
            f.seek(max(0, offset - 4096))
            if sha(f.read(min(offset, 4096))) != cursor["tail_hash"]:
                offset = 0
        f.seek(offset)
        raw = f.read(MAX_BATCH + 1)
        raw = raw[:MAX_BATCH]
        end = raw.rfind(b"\n") + 1
        if not end and raw:
            if len(raw) >= MAX_BATCH:
                raise ValueError("A log event exceeds the maximum batch size.")
            return {"events": [], "cursor": cursor, "count": 0}
        complete = raw[:end].splitlines(keepends=True)[:10000]
        end = sum(map(len, complete))
        lines = b"".join(complete).decode("utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
        if len(events) > 10000:
            raise ValueError("Too many events in one batch; use smaller log records.")
        for event in events:
            if not isinstance(event, dict) or event.get("type") not in {
                "run",
                "metric",
                "artifact",
            }:
                raise ValueError("Expected run, metric or artifact events.")
            for field in ("event_id", "run_id"):
                if not isinstance(event.get(field), str) or not 0 < len(event[field]) <= 200:
                    raise ValueError("Every event needs stable event_id and run_id values.")
            if event["type"] == "metric":
                if (
                    not isinstance(event.get("name"), str)
                    or not event["name"]
                    or not isinstance(event.get("value"), (int, float))
                    or isinstance(event["value"], bool)
                    or not math.isfinite(event["value"])
                ):
                    raise ValueError("Metric samples require a name and finite numeric value.")
                if (
                    not isinstance(event.get("step"), int)
                    or isinstance(event["step"], bool)
                    or event["step"] < 0
                ):
                    raise ValueError("Metric samples require a nonnegative integer step.")
            if len(encoded(event).encode()) > 200_000:
                raise ValueError("One event exceeds 200 KB.")
        after = os.fstat(f.fileno())
        if (after.st_dev, after.st_ino) != (
            before.st_dev,
            before.st_ino,
        ) or after.st_size < offset + end:
            raise Conflict("Log changed incompatibly during this read.")
        f.seek(0)
        fingerprint = sha(f.read(min(offset + end, 4096)))
        f.seek(max(0, offset + end - 4096))
        tail_hash = sha(f.read(min(offset + end, 4096)))
        return {
            "events": events,
            "cursor": {
                "identity": identity,
                "offset": offset + end,
                "prefix_hash": fingerprint,
                "tail_hash": tail_hash,
            },
        }


def commit_log(store, db, source, result):
    count = 0
    for event in result["events"]:
        digest = sha(encoded(event).encode())
        prior = db.execute(
            "SELECT digest FROM integration_events WHERE source=? AND event_id=?",
            (source["id"], event["event_id"]),
        ).fetchone()
        if prior:
            if prior[0] != digest:
                raise Conflict(
                    "An existing event ID changed content. Append a new correction event instead."
                )
            continue
        mapping = db.execute(
            "SELECT record_id FROM integration_objects WHERE source=? AND key=?",
            (source["id"], event["run_id"]),
        ).fetchone()
        if not mapping:
            run = store.put(
                db,
                "run",
                {
                    **RunInput(
                        title=event.get("title") or event["run_id"], status="planned"
                    ).model_dump(),
                    "execution_verified": False,
                    "execution_witness": "local_log_statement",
                    "tracking_source": source["id"],
                    "external_id": event["run_id"],
                },
            )
            db.execute(
                "INSERT INTO integration_objects VALUES (?,?,?,?,?,?)",
                (source["id"], event["run_id"], 1, run["id"], 0, "{}"),
            )
        else:
            run = store.record(db, mapping[0], "run")
        if event["type"] == "run":
            # Preserve author edits: the live source is a separate view.
            values = {**run["data"], "live_source_state": event, "live_updated": time.time()}
            store.put(db, "run", values, identifier=run["id"], expected=run["revision"])
        db.execute(
            "INSERT INTO integration_events VALUES (?,?,?,?,?,?)",
            (source["id"], event["event_id"], digest, run["id"], encoded(event), time.time()),
        )
        count += 1
    return result["cursor"], count


def run_events(store, run_id, after="", limit=1000, before=0):
    with store.db() as db:
        schema(db)
        store.record(db, run_id, "run")
        limit = max(1, min(limit, 2000))
        if after == "latest" or before:
            rows = db.execute(
                "SELECT rowid,event_id,payload FROM integration_events WHERE run_id=? AND (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT ?",
                (run_id, before, before, limit + 1),
            ).fetchall()
            more = len(rows) > limit
            rows = list(reversed(rows[:limit]))
        else:
            rows = db.execute(
                "SELECT rowid,event_id,payload FROM integration_events WHERE run_id=? AND rowid>? ORDER BY rowid LIMIT ?",
                (run_id, int(after or 0), limit + 1),
            ).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
        return {
            "events": [{**json.loads(r["payload"]), "_cursor": str(r["rowid"])} for r in rows],
            "cursor": str(rows[-1]["rowid"]) if rows else ("0" if after == "latest" else after),
            "first_cursor": str(rows[0]["rowid"]) if rows else "",
            "has_more": more,
        }


async def refresh(store, identifier, *, transport=None, only_due=False):
    locks = store.root / "integration-locks"
    locks.mkdir(exist_ok=True)
    lock = FileLock(locks / (sha(identifier.encode()) + ".lock"), timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise Conflict("This source is already syncing.") from exc
    try:
        with store.db() as db:
            schema(db)
            source = store.record(db, identifier, "integration")
            data = source["data"]
            if not data["enabled"]:
                raise Conflict("Reconnect this source before syncing.")
            if only_due and data.get("next_check", 0) > time.time():
                return source
            encrypted = db.execute(
                "SELECT encrypted FROM integration_secrets WHERE source=?", (identifier,)
            ).fetchone()
        try:
            if data["provider"] == "zotero":
                if not encrypted:
                    raise RetryLater("Reconnect Zotero with a read-only key.", state="unauthorized")
                result = await zotero_snapshot(
                    data, cipher(store).decrypt(encrypted[0]).decode(), transport
                )
            else:
                result = await asyncio.to_thread(read_log, data)
            with store.db(write=True) as db:
                current = store.record(db, identifier, "integration")
                if current["revision"] != source["revision"] or not current["data"]["enabled"]:
                    raise Conflict(
                        "Source permissions changed during sync; downloaded bytes were discarded."
                    )
                cursor, count = (commit_zotero if data["provider"] == "zotero" else commit_log)(
                    store, db, source, result
                )
                return store.put(
                    db,
                    "integration",
                    {
                        **data,
                        "cursor": cursor,
                        "state": "current",
                        "error": "",
                        "last_sync": time.time(),
                        "last_count": count,
                        "next_check": time.time()
                        + max(data["interval_seconds"], result.get("cooldown", 0)),
                        "failures": 0,
                    },
                    identifier=identifier,
                    expected=source["revision"],
                )
        except Exception as exc:
            with store.db(write=True) as db:
                current = store.record(db, identifier, "integration")
                if current["revision"] == source["revision"]:
                    failures = data.get("failures", 0) + 1
                    delay = getattr(exc, "delay", min(3600, 30 * 2 ** min(failures, 7)))
                    state = getattr(exc, "state", "retrying")
                    # Never retain HTTP headers, credentials or arbitrary remote exception bodies.
                    message = (
                        str(exc)
                        if isinstance(exc, (RetryLater, ValueError, Conflict))
                        and not isinstance(exc, httpx.HTTPError)
                        else type(exc).__name__
                    )
                    store.put(
                        db,
                        "integration",
                        {
                            **data,
                            "state": state,
                            "error": message[:500],
                            "failures": failures,
                            "next_check": time.time() + delay,
                            "enabled": data["enabled"] and state != "unauthorized",
                        },
                        identifier=identifier,
                        expected=source["revision"],
                    )
            raise
    finally:
        lock.release()


async def background():
    store = Studio()
    while True:
        try:
            with store.db() as db:
                due = db.execute(
                    "SELECT id,scope FROM records WHERE kind='integration' AND json_extract(data,'$.enabled')=1 AND COALESCE(json_extract(data,'$.next_check'),0)<=? LIMIT 20",
                    (time.time(),),
                ).fetchall()
            for row in due:
                try:
                    from deeptutor.services.workspace.activity import data_activity

                    with data_activity():
                        await refresh(
                            Studio(root=store.root, project=row["scope"]), row["id"], only_due=True
                        )
                except Exception:
                    logging.getLogger(__name__).info("Source sync deferred: %s", row["id"])
        except Exception:
            logging.getLogger(__name__).exception("Integration scheduler could not read its queue")
        await asyncio.sleep(10)
