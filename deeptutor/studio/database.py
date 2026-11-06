"""Account-owned catalog, project-scoped records, immutable revisions and dependencies.

Short transactions never span network, parsing, compilation or model work.
Files are content-addressed; only validated domain commands write records.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from deeptutor.multi_user.paths import get_account_path_service
from deeptutor.services.workspace.context import current_workspace_id
from deeptutor.services.workspace.project_recall import tokens

LIBRARY = "@library"
GLOBAL_KINDS = {"work", "edition", "blob", "discovery", "subscription"}


class Conflict(ValueError):
    pass


class Missing(ValueError):
    pass


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
    )


def sha(data: bytes):
    return hashlib.sha256(data).hexdigest()


class Studio:
    def __init__(self, root: Path | None = None, project: str | None = None):
        self.root = root or get_account_path_service().get_user_root() / "studio"
        self.project = current_workspace_id() if project is None else project
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def db(self, *, write=False):
        db = sqlite3.connect(self.root / "studio.sqlite", timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            # Schema installation is a write. Do it only for a new database;
            # normal reads must remain reads, including inside a writer's snapshot.
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
            ).fetchone():
                db.executescript("""
                  CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                  INSERT OR IGNORE INTO metadata VALUES ('schema','1');
                  CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>0), data TEXT NOT NULL CHECK(json_valid(data)),
                    created REAL NOT NULL, updated REAL NOT NULL
                  );
                  CREATE INDEX IF NOT EXISTS records_scope ON records(scope,kind,updated);
                  CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT NOT NULL REFERENCES records(id), revision INTEGER NOT NULL,
                    data TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(id,revision)
                  );
                  CREATE TABLE IF NOT EXISTS identifiers (
                    scheme TEXT NOT NULL, value TEXT NOT NULL,
                    edition_id TEXT NOT NULL REFERENCES records(id), PRIMARY KEY(scheme,value)
                  );
                  CREATE TABLE IF NOT EXISTS dependencies (
                    source TEXT NOT NULL REFERENCES records(id), target TEXT NOT NULL REFERENCES records(id),
                    source_revision INTEGER NOT NULL, relation TEXT NOT NULL,
                    PRIMARY KEY(source,target,relation), CHECK(source!=target)
                  );
                  CREATE TABLE IF NOT EXISTS project_library (
                    project TEXT NOT NULL, edition TEXT NOT NULL REFERENCES records(id),
                    role TEXT NOT NULL DEFAULT 'reading', created REAL NOT NULL,
                    PRIMARY KEY(project,edition)
                  );
                  CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id UNINDEXED, content);
                """)
            if db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0] != "1":
                raise Conflict("This library requires a newer application version.")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def record(self, db, identifier, kind=None):
        row = db.execute(
            "SELECT * FROM records WHERE id=? AND scope IN (?,?)",
            (identifier, self.project, LIBRARY),
        ).fetchone()
        if row is None or (kind and row["kind"] != kind):
            raise Missing("Record not found in this project.")
        return {**dict(row), "data": json.loads(row["data"])}

    def get(self, identifier, kind=None):
        with self.db() as db:
            return self.record(db, identifier, kind)

    def put(self, db, kind, data, *, identifier=None, expected=None):
        payload = encoded(data)
        if len(payload.encode()) > 4_000_000:
            raise ValueError("Record exceeds the supported size.")
        now = time.time()
        scope = LIBRARY if kind in GLOBAL_KINDS else self.project
        if identifier:
            old = self.record(db, identifier, kind)
            if expected != old["revision"]:
                raise Conflict("This record changed. Reload and review the newer revision.")
            revision = old["revision"] + 1
            db.execute(
                "UPDATE records SET data=?,revision=?,updated=? WHERE id=?",
                (payload, revision, now, identifier),
            )
            db.execute("DELETE FROM search WHERE id=?", (identifier,))
        else:
            identifier, revision = uuid4().hex, 1
            db.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?,?)",
                (identifier, kind, scope, revision, payload, now, now),
            )
        db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (identifier, revision, payload, now))
        db.execute("INSERT INTO search VALUES (?,?)", (identifier, " ".join(tokens(payload))))
        return self.record(db, identifier)

    def list(self, kind, query="", limit=200):
        with self.db() as db:
            params = [kind, self.project, LIBRARY]
            sql = "SELECT r.id FROM records r WHERE r.kind=? AND r.scope IN (?,?)"
            terms = tokens(query)[:32]
            if terms:
                sql += " AND r.id IN (SELECT id FROM search WHERE search MATCH ?)"
                params.append(" AND ".join('"' + term + '"' for term in terms))
            sql += " ORDER BY r.updated DESC, r.id LIMIT ?"
            params.append(min(limit, 500))
            return [self.record(db, row[0]) for row in db.execute(sql, params)]

    def link(self, db, source, target, relation="uses"):
        source_row = self.record(db, source)
        target_row = self.record(db, target)
        if target_row["scope"] == LIBRARY and source_row["scope"] != LIBRARY:
            raise ValueError("Project records cannot be dependencies of global library metadata.")
        if source == target:
            raise ValueError("A record cannot depend on itself.")
        cycle = db.execute(
            """WITH RECURSIVE reach(id) AS (
            SELECT target FROM dependencies WHERE source=? UNION
            SELECT d.target FROM dependencies d JOIN reach r ON d.source=r.id
        ) SELECT 1 FROM reach WHERE id=?""",
            (target, source),
        ).fetchone()
        if cycle:
            raise ValueError("Dependency would create a cycle.")
        db.execute(
            "INSERT OR REPLACE INTO dependencies VALUES (?,?,?,?)",
            (source, target, source_row["revision"], relation),
        )

    def impact(self, identifier):
        with self.db() as db:
            self.record(db, identifier)
            rows = db.execute(
                """WITH RECURSIVE reach(id) AS (
                SELECT target FROM dependencies WHERE source=? UNION
                SELECT d.target FROM dependencies d JOIN reach r ON d.source=r.id
            ) SELECT id FROM reach""",
                (identifier,),
            ).fetchall()
            # Never disclose records in another project, even when a global edition changed.
            result = []
            for row in rows:
                try:
                    result.append(self.record(db, row[0]))
                except Missing:
                    pass
            return result

    def stale(self, identifier, db=None):
        if db is None:
            with self.db() as connection:
                return self.stale(identifier, connection)
        self.record(db, identifier)
        rows = db.execute(
            """WITH RECURSIVE ancestors(id) AS (
            SELECT source FROM dependencies WHERE target=? UNION
            SELECT d.source FROM dependencies d JOIN ancestors a ON d.target=a.id
        ) SELECT d.source,d.target,d.source_revision,r.revision FROM dependencies d
          JOIN records r ON r.id=d.source
          WHERE (d.target=? OR d.target IN (SELECT id FROM ancestors))
          AND d.source_revision!=r.revision AND d.relation!='original_edition'""",
            (identifier, identifier),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_blob(self, data: bytes):
        if len(data) > 100 * 1024 * 1024:
            raise ValueError("Files must not exceed 100 MB.")
        digest = sha(data)
        directory = self.root / "blobs" / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / digest
        import tempfile

        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if sha(path.read_bytes()) != digest:
                    raise Conflict("Stored file failed its integrity check.")
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    def blob_path(self, digest):
        import re

        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise Missing("Invalid file identity.")
        path = self.root / "blobs" / digest[:2] / digest
        if not path.is_file() or path.is_symlink():
            raise Missing("File is unavailable.")
        return path
