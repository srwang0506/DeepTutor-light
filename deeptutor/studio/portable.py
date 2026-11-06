"""Encrypted device-to-device project exchange with three-way merge receipts.

Transport is explicit file exchange, independent of a cloud vendor. Every
resource has an immutable incoming hash, a local compare-and-swap precondition,
and a durable receipt. Credentials, source grants and executable jobs are not
portable. Conflicts are never resolved by wall-clock timestamps.
"""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from filelock import FileLock

from .database import LIBRARY, Conflict, encoded, sha

MAGIC = b"DTSYNC1\0"
LIMIT = 200_000_000
KINDS = {
    "work",
    "edition",
    "evidence",
    "observation",
    "run",
    "claim",
    "artifact",
    "screening",
    "manuscript",
    "proposal",
    "submission",
    "review",
    "passage",
    "citation",
    "collection",
    "reading_resume",
    "activity",
    "review_protocol",
    "review_decision",
    "review_extraction",
}
FEATURES = {
    "memory",
    "personal_study",
    "book",
    "learning",
    "reading",
    "timed_media",
    "notebook",
    "co-writer",
    "courses",
    "learning_journal",
    "files",
    "outputs",
    "presentations",
    "attachments",
}
SESSION_TABLES = (
    "sessions",
    "messages",
    "turns",
    "turn_submissions",
    "turn_events",
    "quiz_contracts",
    "quiz_exposures",
    "quiz_submissions",
    "quiz_projection_outbox",
    "notebook_entries",
    "assessment_attempts",
    "quiz_families",
    "quiz_family_reviews",
)


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS portable_seen (peer TEXT NOT NULL, scope TEXT NOT NULL, resource TEXT NOT NULL, remote_hash TEXT NOT NULL, local_hash TEXT NOT NULL, PRIMARY KEY(peer,scope,resource))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS portable_batches (id TEXT PRIMARY KEY, peer TEXT NOT NULL, scope TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS portable_steps (batch TEXT NOT NULL, resource TEXT NOT NULL, choice TEXT NOT NULL, before_hash TEXT NOT NULL, after_hash TEXT, state TEXT NOT NULL, PRIMARY KEY(batch,resource))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS portable_export_inventory (scope TEXT PRIMARY KEY, keys_json TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS portable_clocks (peer TEXT, scope TEXT, generation INTEGER NOT NULL, PRIMARY KEY(peer,scope))"
    )
    row = db.execute("SELECT value FROM metadata WHERE key='portable_device'").fetchone()
    if not row:
        db.execute("INSERT INTO metadata VALUES ('portable_device',?)", (uuid4().hex,))


def key(passphrase, salt):
    if not 12 <= len(passphrase) <= 256:
        raise ValueError("Use a transfer passphrase between 12 and 256 characters.")
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode())


def seal(payload, passphrase):
    raw = encoded(payload).encode()
    if len(raw) > LIMIT:
        raise ValueError(
            "This project exceeds the 200 MB transfer limit. Split large material into smaller projects."
        )
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(key(passphrase, salt)).encrypt(nonce, raw, MAGIC)


def unseal(raw, passphrase):
    from cryptography.exceptions import InvalidTag

    if len(raw) > LIMIT + 100 or not raw.startswith(MAGIC) or len(raw) < 52:
        raise ValueError("Invalid or oversized project transfer package.")
    try:
        payload = json.loads(
            AESGCM(key(passphrase, raw[8:24])).decrypt(raw[24:36], raw[36:], MAGIC)
        )
    except (InvalidTag, ValueError, UnicodeError) as exc:
        raise ValueError("Incorrect passphrase or damaged transfer package.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 1
        or not isinstance(payload.get("project"), str)
        or len(payload.get("project", "")) > 200
        or not isinstance(payload.get("device"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", payload.get("device", ""))
        or not isinstance(payload.get("resources"), dict)
        or len(payload["resources"]) > 20000
        or not isinstance(payload.get("edges", []), list)
        or len(payload.get("edges", [])) > 100000
    ):
        raise ValueError("Unsupported transfer manifest.")
    return payload


def digest(value):
    return sha(encoded(value).encode()) if value is not None else ""


def _path(value):
    path = Path(value)
    if (
        path.is_absolute()
        or not value
        or any(p in {".", ".."} or p.startswith(".") for p in path.parts)
        or "\\" in value
    ):
        raise ValueError("Unsafe transfer path.")
    return path


def file_target(store, resource, paths=None):
    kind, rest = resource.split("/", 1)
    if kind == "manuscript":
        identifier, relative = rest.split("/", 1)
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid manuscript identity.")
        from .manuscripts import relative_path

        relative_path(relative)
        root = store.root / "manuscripts" / identifier / "tree"
    elif kind == "file":
        feature, relative = rest.split("/", 1)
        if feature not in FEATURES or paths is None:
            raise ValueError("This resource cannot be installed in this workspace.")
        from deeptutor.services.workspace.data_migration import _feature_path

        root = _feature_path(paths, feature)
    else:
        raise ValueError("Unknown file resource.")
    child = _path(relative)
    target = root / child
    if (
        root.is_symlink()
        or any(
            p.is_symlink()
            for p in [target, *target.parents]
            if p != root.parent and p.is_relative_to(root)
        )
        or not target.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("Linked transfer destinations are not allowed.")
    return target


def object_payload(store, db, identifier):
    row = store.record(db, identifier)
    history = {
        sha(r["data"].encode()): json.loads(r["data"])
        for r in db.execute("SELECT data FROM revisions WHERE id=?", (identifier,))
    }
    return {
        "kind": row["kind"],
        "scope": "library" if row["scope"] == LIBRARY else "project",
        # Export stored bytes, not presentation-time legacy defaults. A view
        # migration must not silently manufacture a new historical revision.
        "data": json.loads(
            db.execute("SELECT data FROM records WHERE id=?", (identifier,)).fetchone()[0]
        ),
        "history": history,
    }


def session_connection(sessions):
    conn = sqlite3.connect(sessions.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS portable_row_ids (kind TEXT NOT NULL, local_id INTEGER NOT NULL, portable_id TEXT NOT NULL UNIQUE, PRIMARY KEY(kind,local_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS portable_row_aliases (kind TEXT, portable_id TEXT, local_id INTEGER, PRIMARY KEY(kind,portable_id))"
    )
    conn.commit()
    return conn


def row_identity(conn, kind, identifier):
    existing = conn.execute(
        "SELECT portable_id FROM portable_row_ids WHERE kind=? AND local_id=?", (kind, identifier)
    ).fetchone()
    if existing:
        return existing[0]
    value = uuid4().hex
    conn.execute("INSERT INTO portable_row_ids VALUES (?,?,?)", (kind, identifier, value))
    return value


def session_payload(conn, identifier, scope):
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
    if not row:
        return None
    preferences = json.loads(row["preferences_json"] or "{}")
    if preferences.get("workspace_id", "") != scope:
        raise Conflict("A conversation with this identity belongs to another workspace.")
    result = {}
    for table in SESSION_TABLES:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        if table in {"quiz_families", "quiz_family_reviews"}:
            records = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM {table} WHERE workspace=? AND version IN (SELECT version FROM quiz_contracts WHERE session_id=?)",
                    (scope, identifier),
                )
            ]
            for item in records:
                item["workspace"] = ""
        elif table == "quiz_projection_outbox":
            records = [
                dict(r)
                for r in conn.execute(
                    "SELECT p.* FROM quiz_projection_outbox p JOIN quiz_submissions q ON q.attempt_id=p.attempt_id WHERE q.session_id=?",
                    (identifier,),
                )
            ]
        elif table == "turn_events":
            records = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM turn_events WHERE turn_id IN (SELECT id FROM turns WHERE session_id=?) ORDER BY turn_id,seq",
                    (identifier,),
                )
            ]
            for item in records:
                item.pop("id", None)
        else:
            column = "id" if table == "sessions" else "session_id"
            records = [
                dict(r)
                for r in conn.execute(f"SELECT * FROM {table} WHERE {column}=?", (identifier,))
            ]
        result[table] = records
    message_ids = {
        r["id"]: row_identity(conn, "message", r["id"]) for r in result.get("messages", [])
    }
    notebook_ids = {
        r["id"]: row_identity(conn, "notebook", r["id"]) for r in result.get("notebook_entries", [])
    }
    for table, rows in result.items():
        for item in rows:
            if table == "sessions":
                prefs = json.loads(item.get("preferences_json") or "{}")
                for k in ("workspace_id", "memory_policy", "memory_sources", "allowed_sources"):
                    prefs.pop(k, None)
                item["preferences_json"] = encoded(prefs)
                item["summary_up_to_msg_id"] = message_ids.get(item["summary_up_to_msg_id"], 0)
            elif table == "messages":
                item["id"] = message_ids[item["id"]]
                item["parent_message_id"] = message_ids.get(item.get("parent_message_id"))
            elif table == "turns":
                item["assistant_message_id"] = message_ids.get(item.get("assistant_message_id"))
            elif table == "turn_submissions":
                item["workspace_id"] = ""
            elif table == "notebook_entries":
                item["portable_categories"] = [
                    r[0]
                    for r in conn.execute(
                        "SELECT c.name FROM notebook_categories c JOIN notebook_entry_categories n ON c.id=n.category_id WHERE n.entry_id=? ORDER BY c.name",
                        (item["id"],),
                    )
                ]
                item["id"] = notebook_ids[item["id"]]
            elif table == "assessment_attempts":
                item["notebook_entry_id"] = notebook_ids.get(item.get("notebook_entry_id"))
            elif table == "quiz_submissions":
                receipt = json.loads(item["receipt_json"])
                if receipt.get("entry_id") is not None:
                    receipt["entry_id"] = notebook_ids.get(receipt["entry_id"])
                item["receipt_json"] = encoded(receipt)
        result[table] = sorted(rows, key=encoded)
    return result


def transfer_file_bytes(path):
    """Use the same committed SQLite snapshot for export and conflict checks.

    Comparing a backup file to a live database's physical header would report
    false conflicts and miss committed WAL rows. Fresh backups normalize that
    transport header without executing incoming schema.
    """
    raw = path.read_bytes()
    if raw.startswith(b"SQLite format 3\x00"):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "snapshot.db"
            with (
                sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as original,
                sqlite3.connect(snapshot) as target,
            ):
                original.backup(target)
            raw = snapshot.read_bytes()
    return raw


def collect(store, paths=None, sessions=None):
    resources = {}
    with store.db(write=True) as db:
        schema(db)
        device = db.execute("SELECT value FROM metadata WHERE key='portable_device'").fetchone()[0]
        rows = db.execute("SELECT id,kind FROM records WHERE scope=?", (store.project,)).fetchall()
        identifiers = {r["id"] for r in rows if r["kind"] in KINDS}
        identifiers.update(
            r[0]
            for r in db.execute(
                "SELECT edition FROM project_library WHERE project=?", (store.project,)
            )
        )
        pending = list(identifiers)
        while pending:
            identifier = pending.pop()
            row = store.record(db, identifier)
            if row["kind"] not in KINDS:
                continue
            resources["object/" + identifier] = object_payload(store, db, identifier)
            refs = [
                r[0]
                for r in db.execute("SELECT source FROM dependencies WHERE target=?", (identifier,))
            ]
            if row["kind"] == "edition":
                refs.append(row["data"]["work_id"])
            for ref in refs:
                if ref not in identifiers:
                    identifiers.add(ref)
                    pending.append(ref)
        # Frozen render inputs travel inside their data/read sets. Current edges
        # are recreated only when the matching content revision is available.
        edges = [
            dict(r)
            for r in db.execute("SELECT * FROM dependencies")
            if "object/" + r["source"] in resources and "object/" + r["target"] in resources
        ]
        for edge in edges:
            source_data = db.execute(
                "SELECT data FROM revisions WHERE id=? AND revision=?",
                (edge["source"], edge["source_revision"]),
            ).fetchone()
            if source_data:
                edge["source_hash"] = sha(source_data[0].encode())
        clock = db.execute(
            "SELECT value FROM metadata WHERE key=?", ("portable_export_" + store.project,)
        ).fetchone()
        generation = int(clock[0]) + 1 if clock else 1
        previous = db.execute(
            "SELECT keys_json FROM portable_export_inventory WHERE scope=?", (store.project,)
        ).fetchone()
    for resource, value in list(resources.items()):
        if value["kind"] == "manuscript":
            from .manuscripts import file_list, lock, project_file, read_bounded

            identifier = resource.split("/", 1)[1]
            with lock(store, identifier):
                for entry in file_list(store, identifier):
                    raw = read_bounded(project_file(store, identifier, entry["path"]), LIMIT)
                    resources[f"manuscript/{identifier}/{entry['path']}"] = {
                        "bytes": base64.b64encode(raw).decode(),
                        "sha256": sha(raw),
                    }
        # Only content-addressed blobs explicitly referenced by exported objects.
        for value_hash in set(re.findall(r"(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])", encoded(value))):
            try:
                path = store.blob_path(value_hash)
                raw = path.read_bytes()
            except (ValueError, FileNotFoundError):
                continue
            if sha(raw) != value_hash:
                raise ValueError("A referenced original is corrupt.")
            resources["blob/" + value_hash] = {
                "bytes": base64.b64encode(raw).decode(),
                "sha256": value_hash,
            }
    tracked = []
    if paths:
        from deeptutor.services.workspace.data_migration import _feature_path

        for feature in sorted(FEATURES):
            root = _feature_path(paths, feature)
            if not root.is_dir():
                continue
            for file in root.rglob("*"):
                if file.is_symlink():
                    raise ValueError("Materialize linked workspace files before transfer.")
                if (
                    not file.is_file()
                    or any(p.startswith(".") for p in file.relative_to(root).parts)
                    or file.name.endswith(("-wal", "-shm"))
                ):
                    continue
                if file.stat().st_size > 50_000_000:
                    raise ValueError(
                        "A workspace file exceeds 50 MB; use a separate transfer for large assets."
                    )
                raw = transfer_file_bytes(file)
                tracked.append((file, sha(raw)))
                resources[f"file/{feature}/{file.relative_to(root).as_posix()}"] = {
                    "bytes": base64.b64encode(raw).decode(),
                    "sha256": sha(raw),
                }
    if sessions:
        with session_connection(sessions) as conn:
            conn.execute("BEGIN IMMEDIATE")
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM sessions WHERE COALESCE(json_extract(preferences_json,'$.workspace_id'),'')=?",
                    (store.project,),
                )
            ]
            for identifier in ids:
                resources["session/" + identifier] = session_payload(
                    conn, identifier, store.project
                )
    if any(sha(transfer_file_bytes(path)) != value for path, value in tracked):
        raise Conflict("Workspace files changed during export. Retry when edits are saved.")
    # Tombstones are retained as data and shown in the import preview. They do
    # not silently delete library entries shared by other projects.
    for resource in json.loads(previous[0]) if previous else []:
        if resource not in resources:
            resources[resource] = None
    payload = {
        "schema": 1,
        "generation": generation,
        "device": device,
        "project": store.project,
        "created": time.time(),
        "resources": resources,
        "edges": edges,
    }
    if len(encoded(payload).encode()) > LIMIT:
        raise ValueError("Project transfer exceeds 200 MB.")
    return payload


def export(store, passphrase, paths=None, sessions=None):
    with FileLock(store.root / ".portable.lock", timeout=0):
        payload = collect(store, paths, sessions)
        raw = seal(payload, passphrase)
        with store.db(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                ("portable_export_" + store.project, str(payload["generation"])),
            )
            db.execute(
                "INSERT OR REPLACE INTO portable_export_inventory VALUES (?,?)",
                (store.project, encoded(sorted(payload["resources"]))),
            )
        return raw


def local_resource(store, resource, paths=None, sessions=None):
    kind, identifier = resource.split("/", 1)
    if kind == "object":
        with store.db() as db:
            row = db.execute("SELECT id,scope FROM records WHERE id=?", (identifier,)).fetchone()
            if not row:
                return None
            return object_payload(store, db, identifier)
    if kind == "blob":
        try:
            raw = store.blob_path(identifier).read_bytes()
        except (ValueError, FileNotFoundError):
            return None
        return {"bytes": base64.b64encode(raw).decode(), "sha256": sha(raw)}
    if kind == "session":
        if not sessions:
            raise ValueError("Session transfer is unavailable in this runtime.")
        with session_connection(sessions) as conn:
            return session_payload(conn, identifier, store.project)
    path = file_target(store, resource, paths)
    if not path.exists():
        return None
    raw = transfer_file_bytes(path)
    return {"bytes": base64.b64encode(raw).decode(), "sha256": sha(raw)}


def validate(resource, value):
    if not isinstance(resource, str) or "/" not in resource or len(resource) > 2000:
        raise ValueError("Invalid resource key.")
    kind, identifier = resource.split("/", 1)
    if kind not in {"object", "blob", "session", "file", "manuscript"}:
        raise ValueError("Unsupported portable resource.")
    if kind == "object" and not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid portable object identity.")
    if kind == "session" and (not identifier or len(identifier) > 200 or "/" in identifier):
        raise ValueError("Invalid conversation identity.")
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("Portable resource must be an object.")
    if kind == "object":
        if (
            value.get("kind") not in KINDS
            or value.get("scope") not in {"library", "project"}
            or not isinstance(value.get("data"), dict)
            or not isinstance(value.get("history"), dict)
            or len(encoded(value).encode()) > 20_000_000
        ):
            raise ValueError("Unsupported object payload.")
        if any(
            not isinstance(data, dict) or sha(encoded(data).encode()) != checksum
            for checksum, data in value["history"].items()
        ):
            raise ValueError("Historical revision checksum mismatch.")
        if (value["kind"] in {"work", "edition"}) != (value["scope"] == "library"):
            raise ValueError("Invalid object ownership scope.")
    elif kind == "session":
        if any(
            not isinstance(rows, list)
            or len(rows) > 100000
            or any(not isinstance(row, dict) for row in rows)
            for rows in value.values()
        ):
            raise ValueError("Invalid conversation rows.")
        if (
            not isinstance(value, dict)
            or not set(value) <= set(SESSION_TABLES)
            or len(value.get("sessions", [])) != 1
            or value["sessions"][0].get("id") != identifier
        ):
            raise ValueError("Invalid conversation payload.")
        submissions = {r.get("attempt_id"): r for r in value.get("quiz_submissions", [])}
        for pending in value.get("quiz_projection_outbox", []):
            submission = submissions.get(pending.get("attempt_id"))
            try:
                projection = json.loads(pending["payload_json"])
                valid = (
                    submission
                    and submission.get("state") == "committed"
                    and json.loads(submission["receipt_json"]).get("projection_status") == "pending"
                    and projection["session_id"] == identifier
                    and isinstance(projection["item"], dict)
                    and isinstance(projection["attempt"], dict)
                )
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                raise ValueError("Invalid pending grade projection.")
        turns = {r.get("id") for r in value.get("turns", [])}
        versions = {r.get("version") for r in value.get("quiz_contracts", [])}
        if None in turns:
            raise ValueError("Missing turn identity.")
        if any(r.get("turn_id") not in turns for r in value.get("turn_events", [])):
            raise ValueError("Turn events escape the incoming conversation.")
        for table, rows in value.items():
            if (
                not isinstance(rows, list)
                or len(rows) > 100000
                or any(not isinstance(row, dict) for row in rows)
            ):
                raise ValueError("Oversized conversation.")
            if any(
                row.get("session_id") != identifier
                for row in rows
                if table
                not in {
                    "sessions",
                    "turn_events",
                    "quiz_families",
                    "quiz_family_reviews",
                    "quiz_projection_outbox",
                }
            ):
                raise ValueError("Conversation payload crosses session boundaries.")
            if table in {"quiz_families", "quiz_family_reviews"} and any(
                row.get("version") not in versions or row.get("workspace") != "" for row in rows
            ):
                raise ValueError("Question families escape the incoming conversation.")
    else:
        try:
            raw = base64.b64decode(value["bytes"], validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("Invalid file payload.") from exc
        if (
            len(raw) > 50_000_000
            or sha(raw) != value.get("sha256")
            or (kind == "blob" and identifier != sha(raw))
        ):
            raise ValueError("Transfer file checksum mismatch.")


def peer(payload):
    return payload["device"] + ":" + sha(payload["project"].encode())


def preview(store, raw, passphrase, paths=None, sessions=None):
    payload = unseal(raw, passphrase)
    batch = sha(encoded(payload).encode())
    with store.db(write=True) as db:
        schema(db)
        generation = payload.get("generation")
        if not isinstance(generation, int) or generation < 1:
            raise ValueError("Transfer generation is missing.")
        clock = db.execute(
            "SELECT generation FROM portable_clocks WHERE peer=? AND scope=?",
            (peer(payload), store.project),
        ).fetchone()
        if clock and generation < clock[0]:
            raise Conflict(
                "This package predates an already applied transfer. Export a fresh package."
            )
        old = db.execute("SELECT state,scope FROM portable_batches WHERE id=?", (batch,)).fetchone()
        if old and old["scope"] != store.project:
            raise Conflict("This package is already bound to another destination workspace.")
        if old and old["state"] == "complete":
            return {"batch_id": batch, "state": "complete", "items": []}
        seen = {
            r["resource"]: dict(r)
            for r in db.execute(
                "SELECT * FROM portable_seen WHERE peer=? AND scope=?",
                (peer(payload), store.project),
            )
        }
    for edge in payload.get("edges", []):
        if (
            not isinstance(edge, dict)
            or not all(
                isinstance(edge.get(k), str)
                for k in ("source", "target", "relation", "source_hash")
            )
            or "object/" + edge.get("source", "") not in payload["resources"]
            or "object/" + edge.get("target", "") not in payload["resources"]
        ):
            raise ValueError("Dependency escapes the package.")
    items = []
    for resource, value in payload["resources"].items():
        validate(resource, value)
        current = local_resource(store, resource, paths, sessions)
        before, incoming = digest(current), digest(value)
        previous = seen.get(resource)
        if before == incoming or previous and incoming == previous["remote_hash"]:
            state = "unchanged"
        elif not before or previous and before == previous["local_hash"]:
            state = "delete" if value is None else "update" if before else "new"
        else:
            state = "conflict"
        items.append(
            {"resource": resource, "state": state, "local_hash": before, "incoming_hash": incoming}
        )
    payload["preview"] = items
    with store.db(write=True) as db:
        schema(db)
        # A fresh preview is the user's new compare-and-swap boundary. Retain
        # completed receipts, but let unapplied steps use the new decision and
        # current hash after an interruption or an external edit.
        db.execute("DELETE FROM portable_steps WHERE batch=? AND state='prepared'", (batch,))
        db.execute(
            "INSERT INTO portable_batches VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (batch, peer(payload), store.project, encoded(payload), "prepared", time.time()),
        )
    return {
        "batch_id": batch,
        "device": payload["device"],
        "source_project": payload["project"],
        "state": "prepared",
        "items": items,
    }


def atomic_file(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".portable-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def install_session(sessions, identifier, value, workspace, expected=None):
    with session_connection(sessions) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        if (
            expected is not None
            and digest(session_payload(conn, identifier, workspace)) != expected
        ):
            raise Conflict("Conversation changed during import; preview again.")
        if value is None:
            current = conn.execute(
                "SELECT preferences_json FROM sessions WHERE id=?", (identifier,)
            ).fetchone()
            if current:
                prefs = json.loads(current[0] or "{}")
                prefs["archived"] = True
                conn.execute(
                    "UPDATE sessions SET preferences_json=? WHERE id=?",
                    (encoded(prefs), identifier),
                )
            return
        maps = {}
        for kind, table in [("message", "messages"), ("notebook", "notebook_entries")]:
            mappings = {}
            next_id = conn.execute(f"SELECT COALESCE(MAX(id),0)+1 FROM {table}").fetchone()[0]
            for row in value.get(table, []):
                external = row["id"]
                if not isinstance(external, str) or not re.fullmatch(r"[a-f0-9]{32}", external):
                    raise ValueError("Invalid portable row identity.")
                existing = conn.execute(
                    "SELECT local_id FROM portable_row_ids WHERE kind=? AND portable_id=? UNION SELECT local_id FROM portable_row_aliases WHERE kind=? AND portable_id=?",
                    (kind, external, kind, external),
                ).fetchone()
                if existing:
                    local = existing[0]
                    owner = conn.execute(
                        f"SELECT session_id FROM {table} WHERE id=?", (local,)
                    ).fetchone()
                    if owner and owner[0] != identifier:
                        raise Conflict("A portable row belongs to a different conversation.")
                else:
                    # A notebook answer may have arrived through another device
                    # before UUID mapping existed. Keep its stable semantic row.
                    semantic = None
                    if kind == "notebook":
                        semantic = conn.execute(
                            "SELECT id,session_id FROM notebook_entries WHERE origin_type=? AND origin_ref=? AND turn_id=? AND question_id=?",
                            tuple(
                                row.get(k)
                                for k in ("origin_type", "origin_ref", "turn_id", "question_id")
                            ),
                        ).fetchone()
                    if semantic and semantic["session_id"] != identifier:
                        raise Conflict("A notebook answer belongs to a different conversation.")
                    local = semantic["id"] if semantic else next_id
                    if not semantic:
                        next_id += 1
                    conn.execute(
                        "INSERT OR IGNORE INTO portable_row_ids VALUES (?,?,?)",
                        (kind, local, external),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO portable_row_aliases VALUES (?,?,?)",
                        (kind, external, local),
                    )
                mappings[external] = local
            maps[kind] = mappings
        # Upsert imported rows without deleting local history or notebook links.
        # Entire-thread conflicts are reviewed before this transaction begins.
        for table in SESSION_TABLES:
            if table == "quiz_families":
                continue  # Install the reviewed head after merging its history.
            rows = value.get(table, [])
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {r[1] for r in info}
            primary = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            for original in rows:
                row = dict(original)
                categories = (
                    row.pop("portable_categories", []) if table == "notebook_entries" else []
                )
                if not isinstance(categories, list) or any(
                    not isinstance(c, str) or len(c) > 200 for c in categories
                ):
                    raise ValueError("Invalid notebook category names.")
                if not set(row) <= columns:
                    raise ValueError("Conversation schema is newer than this application.")
                if table == "sessions":
                    prefs = json.loads(row.get("preferences_json") or "{}")
                    for field in ("memory_policy", "memory_sources", "allowed_sources"):
                        prefs.pop(field, None)
                    prefs["workspace_id"] = workspace
                    row["preferences_json"] = encoded(prefs)
                    row["summary_up_to_msg_id"] = maps["message"].get(
                        row["summary_up_to_msg_id"], 0
                    )
                elif table == "messages":
                    row["id"] = maps["message"][row["id"]]
                    row["parent_message_id"] = maps["message"].get(row.get("parent_message_id"))
                elif table == "notebook_entries":
                    row["id"] = maps["notebook"][row["id"]]
                elif table == "assessment_attempts":
                    row["notebook_entry_id"] = maps["notebook"].get(row.get("notebook_entry_id"))
                elif table == "turns":
                    row["assistant_message_id"] = maps["message"].get(
                        row.get("assistant_message_id")
                    )
                    if row.get("status") not in {"completed", "failed", "cancelled"}:
                        row["status"] = "cancelled"
                elif table == "turn_submissions":
                    row["workspace_id"] = workspace
                elif table == "quiz_submissions" and row.get("state") in {"grading", "prepared"}:
                    row.update(state="cancelled", receipt_json="{}")
                if table == "quiz_submissions":
                    receipt = json.loads(row["receipt_json"])
                    if receipt.get("entry_id") is not None:
                        receipt["entry_id"] = maps["notebook"].get(receipt["entry_id"])
                    row["receipt_json"] = encoded(receipt)
                if table in {"quiz_families", "quiz_family_reviews"}:
                    row["workspace"] = workspace
                    # Historical reviews are append-only. Device-local revision
                    # numbers are remapped when both devices reviewed this family.
                    if table == "quiz_family_reviews":
                        same = conn.execute(
                            "SELECT 1 FROM quiz_family_reviews WHERE workspace=? AND version=? AND descriptor=? AND reviewed=?",
                            (workspace, row["version"], row["descriptor"], row["reviewed"]),
                        ).fetchone()
                        if same:
                            continue
                        row["revision"] = conn.execute(
                            "SELECT COALESCE(MAX(revision),0)+1 FROM quiz_family_reviews WHERE workspace=? AND version=?",
                            (workspace, row["version"]),
                        ).fetchone()[0]
                if table == "turn_events":
                    primary = ["turn_id", "seq"]
                from deeptutor.services.workspace.references import rebind_local_urls

                for field, text in row.items():
                    if isinstance(text, str):
                        row[field] = rebind_local_urls(text, workspace)
                if "session_id" in columns and primary:
                    where = " AND ".join(n + "=?" for n in primary)
                    prior = conn.execute(
                        f"SELECT session_id FROM {table} WHERE {where}", [row[n] for n in primary]
                    ).fetchone()
                    if prior and prior[0] != identifier:
                        raise Conflict("A portable receipt identity belongs to another session.")
                names = list(row)
                updates = [n + "=excluded." + n for n in names if n not in primary]
                query = f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' for _ in names)}) ON CONFLICT ({','.join(primary)}) DO UPDATE SET {','.join(updates)}"
                conn.execute(query, [row[n] for n in names])
                if table == "notebook_entries":
                    conn.execute(
                        "DELETE FROM notebook_entry_categories WHERE entry_id=?", (row["id"],)
                    )
                    for category in categories:
                        conn.execute(
                            "INSERT OR IGNORE INTO notebook_categories(name,created_at) VALUES (?,?)",
                            (category, time.time()),
                        )
                        conn.execute(
                            "INSERT OR IGNORE INTO notebook_entry_categories VALUES (?,(SELECT id FROM notebook_categories WHERE name=?))",
                            (row["id"], category),
                        )
        # Continue local review numbering after the merged append-only history.
        for head in value.get("quiz_families", []):
            latest = conn.execute(
                "SELECT * FROM quiz_family_reviews WHERE workspace=? AND version=? ORDER BY revision DESC LIMIT 1",
                (workspace, head["version"]),
            ).fetchone()
            revision = latest["revision"] if latest else 0
            same = latest and all(
                latest[k] == head[k] for k in ("family", "objective", "descriptor", "reviewed")
            )
            if not same:
                revision += 1
            values = (
                workspace,
                head["version"],
                head["family"],
                head["objective"],
                head["descriptor"],
                revision,
                head["reviewed"],
            )
            if not same:
                conn.execute("INSERT INTO quiz_family_reviews VALUES (?,?,?,?,?,?,?)", values)
            conn.execute("INSERT OR REPLACE INTO quiz_families VALUES (?,?,?,?,?,?,?)", values)


def install(store, resource, value, paths=None, sessions=None, expected=None):
    kind, identifier = resource.split("/", 1)
    if kind == "blob":
        if value is not None:
            store.save_blob(base64.b64decode(value["bytes"]))
        return
    if kind == "session":
        install_session(sessions, identifier, value, store.project, expected)
        return
    if kind == "object":
        with store.db(write=True) as db:
            existing = db.execute("SELECT id FROM records WHERE id=?", (identifier,)).fetchone()
            if (
                expected is not None
                and digest(object_payload(store, db, identifier) if existing else None) != expected
            ):
                raise Conflict("Object changed during import; preview again.")
            if value is None:
                # Reversible transport tombstone; retain history and shared originals.
                db.execute(
                    "CREATE TABLE IF NOT EXISTS portable_tombstones (scope TEXT, resource TEXT, created REAL, PRIMARY KEY(scope,resource))"
                )
                db.execute(
                    "INSERT OR REPLACE INTO portable_tombstones VALUES (?,?,?)",
                    (store.project, resource, time.time()),
                )
                return
            data = value["data"]
            if existing:
                old = store.record(db, identifier)
                if old["kind"] != value["kind"]:
                    raise Conflict("Object kinds cannot change across devices.")
                revision = old["revision"]
            else:
                revision = 0
            known = {
                sha(r[0].encode())
                for r in db.execute("SELECT data FROM revisions WHERE id=?", (identifier,))
            }
            now = time.time()
            scope = LIBRARY if value["scope"] == "library" else store.project
            body = encoded(data)
            if not existing:
                db.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?,?,?)",
                    (identifier, value["kind"], scope, 1, body, now, now),
                )
            for checksum, historical in sorted(value["history"].items()):
                if checksum not in known and checksum != sha(body.encode()):
                    revision += 1
                    db.execute(
                        "INSERT INTO revisions VALUES (?,?,?,?)",
                        (identifier, revision, encoded(historical), now),
                    )
            # Append the incoming head after imported history; numeric revisions
            # are device-local, dependency correspondence uses content hashes.
            revision += 1
            db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (identifier, revision, body, now))
            db.execute(
                "UPDATE records SET revision=?,data=?,updated=? WHERE id=?",
                (revision, body, now, identifier),
            )
            from deeptutor.services.workspace.project_recall import tokens

            db.execute("DELETE FROM search WHERE id=?", (identifier,))
            db.execute("INSERT INTO search VALUES (?,?)", (identifier, " ".join(tokens(body))))
            if value["kind"] == "edition":
                db.execute(
                    "INSERT OR IGNORE INTO project_library VALUES (?,?,?,?)",
                    (store.project, identifier, "reading", time.time()),
                )
                for scheme in ("doi", "arxiv"):
                    if data.get(scheme):
                        existing_id = db.execute(
                            "SELECT edition_id FROM identifiers WHERE scheme=? AND value=?",
                            (scheme, data[scheme]),
                        ).fetchone()
                        if existing_id and existing_id[0] != identifier:
                            raise Conflict(
                                "An imported identifier belongs to another local edition; resolve the library duplicate first."
                            )
                        db.execute(
                            "INSERT OR IGNORE INTO identifiers VALUES (?,?,?)",
                            (scheme, data[scheme], identifier),
                        )
        return
    path = file_target(store, resource, paths)
    if (
        expected is not None
        and digest(local_resource(store, resource, paths, sessions)) != expected
    ):
        raise Conflict("File changed during import; preview again.")
    if path.exists():
        trash = store.root / "portable-history" / sha(resource.encode()) / sha(path.read_bytes())
        if not trash.exists():
            trash.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, trash)
    if value is None:
        if path.exists():
            path.unlink()
    else:
        raw = base64.b64decode(value["bytes"])
        if raw.startswith(b"SQLite format 3\x00"):
            # Never swap an open WAL database. A full feature database is an
            # explicit conflict until its owning workspace is quiescent.
            for suffix in ("-wal", "-shm"):
                if Path(str(path) + suffix).exists():
                    raise Conflict(
                        "Close this feature on the destination before replacing its database."
                    )
            with tempfile.TemporaryDirectory() as temp:
                snapshot = Path(temp) / "snapshot.db"
                snapshot.write_bytes(raw)
                with sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
                    db.execute("PRAGMA trusted_schema=OFF")
                    if db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')"
                    ).fetchone():
                        raise ValueError(
                            "Portable feature databases may not contain executable views or triggers."
                        )
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Incoming database failed integrity verification.")
                from deeptutor.services.workspace.data_migration import _rebind_feature_urls

                _rebind_feature_urls(Path(temp), store.project)
                raw = snapshot.read_bytes()
        elif kind == "file" and path.suffix.lower() in {".json", ".md", ".txt", ".html"}:
            from deeptutor.services.workspace.references import rebind_local_urls

            try:
                raw = rebind_local_urls(raw.decode("utf-8"), store.project).encode()
            except UnicodeError:
                pass
        if (
            expected is not None
            and digest(local_resource(store, resource, paths, sessions)) != expected
        ):
            raise Conflict("File changed during import; preview again.")
        atomic_file(path, raw)


def apply(store, batch, choices, paths=None, sessions=None):
    with FileLock(store.root / ".portable.lock", timeout=0):
        with store.db() as db:
            schema(db)
            row = db.execute(
                "SELECT * FROM portable_batches WHERE id=? AND scope=?", (batch, store.project)
            ).fetchone()
            if not row:
                raise ValueError("Preview this transfer package in the current workspace first.")
            if row["state"] == "complete":
                return json.loads(row["payload"]).get(
                    "result", {"state": "complete", "batch_id": batch}
                )
            payload = json.loads(row["payload"])
        items = sorted(
            payload["preview"],
            key=lambda i: (i["resource"].split("/")[0] not in {"blob", "object"}, i["resource"]),
        )
        for item in items:
            resource = item["resource"]
            value = payload["resources"][resource]
            choice = choices.get(resource)
            if item["state"] == "conflict" and choice not in {"keep", "incoming"}:
                raise Conflict("Resolve every conflict explicitly before applying the package.")
            if choice is not None and choice not in {"keep", "incoming"}:
                raise ValueError("Unsupported conflict resolution.")
        for item in items:
            resource = item["resource"]
            value = payload["resources"][resource]
            with store.db() as db:
                step = db.execute(
                    "SELECT * FROM portable_steps WHERE batch=? AND resource=?", (batch, resource)
                ).fetchone()
            if step and step["state"] == "complete":
                continue
            current = digest(local_resource(store, resource, paths, sessions))
            choice = (
                step["choice"]
                if step
                else choices.get(resource, "keep" if item["state"] == "unchanged" else "incoming")
            )
            expected = step["before_hash"] if step else item["local_hash"]
            # A prepared step whose replacement already landed is safely replayed.
            if current != expected and not (step and current == item["incoming_hash"]):
                raise Conflict(f"Local content changed after preview: {resource}. Preview again.")
            with store.db(write=True) as db:
                db.execute(
                    "INSERT OR IGNORE INTO portable_steps VALUES (?,?,?,?,?,?)",
                    (batch, resource, choice, expected, None, "prepared"),
                )
            if choice == "incoming" and current != item["incoming_hash"]:
                install(store, resource, value, paths, sessions, expected=current)
            after = digest(local_resource(store, resource, paths, sessions))
            with store.db(write=True) as db:
                db.execute(
                    "UPDATE portable_steps SET state=?,after_hash=? WHERE batch=? AND resource=?",
                    ("complete", after, batch, resource),
                )
                db.execute(
                    "INSERT OR REPLACE INTO portable_seen VALUES (?,?,?,?,?)",
                    (
                        peer(payload),
                        store.project,
                        resource,
                        item["incoming_hash"],
                        after if choice == "incoming" or item["state"] == "unchanged" else "",
                    ),
                )
        # Reconnect only edges whose consumed source bytes exist locally. Unknown
        # historical inputs remain in the frozen read set, never retargeted to latest.
        with store.db(write=True) as db:
            unresolved = []
            for edge in payload.get("edges", []):
                target_step = db.execute(
                    "SELECT choice FROM portable_steps WHERE batch=? AND resource=?",
                    (batch, "object/" + edge["target"]),
                ).fetchone()
                if target_step and target_step["choice"] == "keep":
                    # Keeping the local artifact also keeps its local lineage.
                    continue
                if (
                    "object/" + edge.get("source", "") not in payload["resources"]
                    or "object/" + edge.get("target", "") not in payload["resources"]
                ):
                    raise ValueError("Dependency escapes the package.")
                revision = next(
                    (
                        r["revision"]
                        for r in db.execute(
                            "SELECT revision,data FROM revisions WHERE id=?", (edge["source"],)
                        )
                        if sha(r["data"].encode()) == edge.get("source_hash")
                    ),
                    None,
                )
                if revision:
                    try:
                        store.link(
                            db,
                            edge["source"],
                            edge["target"],
                            edge["relation"],
                            source_revision=revision,
                        )
                    except ValueError:
                        unresolved.append(edge)
                else:
                    unresolved.append(edge)
            payload["result"] = {
                "state": "complete",
                "batch_id": batch,
                "unresolved_historical_edges": unresolved,
                "transport": "encrypted_file_exchange",
            }
            db.execute(
                "UPDATE portable_batches SET state=?,payload=? WHERE id=?",
                ("complete", encoded(payload), batch),
            )
            db.execute(
                "INSERT INTO portable_clocks VALUES (?,?,?) ON CONFLICT(peer,scope) DO UPDATE SET generation=MAX(generation,excluded.generation)",
                (peer(payload), store.project, payload["generation"]),
            )
        return {
            "state": "complete",
            "batch_id": batch,
            "unresolved_historical_edges": unresolved,
            "transport": "encrypted_file_exchange",
        }
