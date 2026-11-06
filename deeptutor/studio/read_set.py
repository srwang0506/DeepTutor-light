"""Immutable inputs captured in one short SQLite snapshot, never across model/renderer work."""

from dataclasses import dataclass
import json

from .database import Missing, encoded, sha


@dataclass(frozen=True)
class ReadSet:
    payload: str

    @classmethod
    def capture(cls, store, identifiers, *, revisions=None, locators=None, rules=None):
        with store.db() as db:
            rows, stale = {}, {}
            pending = list(identifiers)
            while pending:
                identifier = pending.pop()
                if identifier in rows:
                    continue
                row = store.record(db, identifier)
                revision = (revisions or {}).get(identifier, row["revision"])
                if revision != row["revision"]:
                    old = db.execute(
                        "SELECT data FROM revisions WHERE id=? AND revision=?",
                        (identifier, revision),
                    ).fetchone()
                    if old is None:
                        raise Missing("The requested source revision is unavailable.")
                    row = store.present({**row, "revision": revision, "data": json.loads(old[0])})
                rows[identifier] = row
                stale[identifier] = store.stale(identifier, db)
                pending.extend(
                    r[0]
                    for r in db.execute(
                        "SELECT source FROM dependencies WHERE target=?", (identifier,)
                    )
                )
            return cls(
                encoded(
                    {
                        "schema": 1,
                        "records": rows,
                        "stale": stale,
                        "locators": locators or {},
                        "rules": rules or {},
                    }
                )
            )

    def get(self, identifier, kind=None):
        row = json.loads(self.payload)["records"].get(identifier)
        if row is None or (kind and row["kind"] != kind):
            raise Missing("Source is not present in this read set.")
        return row

    def stale(self, identifier):
        return json.loads(self.payload)["stale"].get(identifier, [])

    def manifest(self):
        return {**json.loads(self.payload), "digest": sha(self.payload.encode())}

    def attach(self, store, db, target, identifiers, relation):
        for identifier in identifiers:
            store.link(
                db, identifier, target, relation, source_revision=self.get(identifier)["revision"]
            )
