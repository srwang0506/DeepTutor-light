"""Durable build snapshots with bounded workers, cancellation, and crash recovery.

A per-build OS lease distinguishes a live worker from a crashed process. Queued
snapshots resume on startup or project access. Interrupted execution requires an
explicit retry; retries use the same frozen inputs, never a changed working tree.
"""

import threading
import time

from filelock import FileLock, Timeout

from . import manuscripts
from .database import Conflict, Studio

_WORKERS = threading.BoundedSemaphore(2)
_ACTIVE = set()
_LOCK = threading.Lock()
_STOP = threading.Event()


def lease(store, identifier):
    directory = store.root / "build-locks"
    directory.mkdir(exist_ok=True)
    return FileLock(directory / f"{identifier}.lock", timeout=0)


def run(store, identifier):
    try:
        with _WORKERS, lease(store, identifier):
            with store.db(write=True) as db:
                row = store.record(db, identifier, "build")
                if row["data"]["status"] != "queued":
                    return
                row = store.put(
                    db,
                    "build",
                    {**row["data"], "status": "running", "worker_started": time.time()},
                    identifier=identifier,
                    expected=row["revision"],
                )

            def cancelled():
                return _STOP.is_set() or store.get(identifier, "build")["data"].get(
                    "cancel_requested", False
                )

            try:
                manuscripts.execute_build(store, row, cancelled)
            except Exception as exc:
                # Unexpected failures remain inspectable instead of a forever-running spinner.
                digest = store.save_blob(
                    f"Build worker failed: {type(exc).__name__}: {exc}".encode()
                )
                with store.db(write=True) as db:
                    row = store.record(db, identifier, "build")
                    store.put(
                        db,
                        "build",
                        {
                            **row["data"],
                            "status": "failed",
                            "finished": time.time(),
                            "outputs": {"build.log": digest},
                        },
                        identifier=identifier,
                        expected=row["revision"],
                    )
    except Timeout:
        pass  # Another process already owns this durable job.
    finally:
        with _LOCK:
            _ACTIVE.discard((str(store.root), identifier))


def launch(store, identifier):
    if _STOP.is_set():
        return
    key = (str(store.root), identifier)
    with _LOCK:
        if key in _ACTIVE:
            return
        _ACTIVE.add(key)
    # Store scope is captured explicitly; worker threads cannot inherit HTTP contextvars.
    threading.Thread(
        target=run,
        args=(Studio(store.root, store.project), identifier),
        daemon=True,
        name=f"studio-build-{identifier[:8]}",
    ).start()


def resume(store):
    with store.db() as db:
        ids = [
            r[0]
            for r in db.execute(
                "SELECT id FROM records WHERE kind='build' AND scope=? AND json_extract(data,'$.status') IN ('queued','running')",
                (store.project,),
            )
        ]
    for identifier in ids:
        row = store.get(identifier, "build")
        if row["data"]["status"] == "queued":
            launch(store, identifier)
        else:
            try:
                with lease(store, identifier), store.db(write=True) as db:
                    row = store.record(db, identifier, "build")
                    if row["data"]["status"] != "running":
                        continue
                    store.put(
                        db,
                        "build",
                        {
                            **row["data"],
                            "status": "interrupted",
                            "finished": time.time(),
                            "error": "The worker stopped before recording completion. Retry the frozen input snapshot.",
                        },
                        identifier=identifier,
                        expected=row["revision"],
                    )
            except Timeout:
                pass


def enqueue(store, manuscript):
    # Serialize duplicate requests in separate HTTP workers.
    with manuscripts.lock(store, manuscript):
        resume(store)
        with store.db() as db:
            existing = db.execute(
                "SELECT id FROM records WHERE kind='build' AND scope=? AND json_extract(data,'$.manuscript_id')=? AND json_extract(data,'$.status') IN ('queued','running') ORDER BY created LIMIT 1",
                (store.project, manuscript),
            ).fetchone()
            if existing:
                return store.record(db, existing[0], "build")
        row = manuscripts.prepare_build(store, manuscript)
        launch(store, row["id"])
        return row


def cancel(store, identifier):
    with store.db(write=True) as db:
        row = store.record(db, identifier, "build")
        if row["data"]["status"] not in {"queued", "running"}:
            return row
        data = {**row["data"], "cancel_requested": True}
        if data["status"] == "queued":
            data.update(status="cancelled", finished=time.time())
        return store.put(db, "build", data, identifier=identifier, expected=row["revision"])


def retry(store, identifier):
    original = store.get(identifier, "build")
    if original["data"]["status"] not in {"failed", "interrupted", "cancelled"}:
        raise Conflict("Only failed, cancelled or interrupted builds can be retried.")
    with manuscripts.lock(store, original["data"]["manuscript_id"]):
        resume(store)
        with store.db() as db:
            active = db.execute(
                "SELECT id FROM records WHERE kind='build' AND scope=? AND json_extract(data,'$.manuscript_id')=? AND json_extract(data,'$.status') IN ('queued','running') ORDER BY created LIMIT 1",
                (store.project, original["data"]["manuscript_id"]),
            ).fetchone()
            if active:
                current = store.record(db, active[0], "build")
                if current["data"].get("retry_of") == identifier:
                    return current
                raise Conflict("A build is already active for this manuscript.")
        data = {
            k: original["data"][k] for k in ("manuscript_id", "input_files", "engine", "main_file")
        }
        with store.db(write=True) as db:
            row = store.put(
                db,
                "build",
                {
                    **data,
                    "status": "queued",
                    "started": time.time(),
                    "retry_of": identifier,
                    "cancel_requested": False,
                },
            )
        launch(store, row["id"])
        return row


def start():
    _STOP.clear()
    store = Studio()
    with store.db() as db:
        scopes = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT scope FROM records WHERE kind='build' AND json_extract(data,'$.status') IN ('queued','running')"
            )
        ]
    for scope in scopes:
        resume(Studio(store.root, scope))


def shutdown():
    _STOP.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with _LOCK:
            if not _ACTIVE:
                return
        time.sleep(0.05)
