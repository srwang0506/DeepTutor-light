"""Import events are immutable identities, separate from the author's editable objects."""

from .database import Conflict, encoded, sha


def lookup(store, db, connector, key, payload_hash):
    found = db.execute(
        "SELECT id FROM records WHERE scope=? AND kind='import_receipt' "
        "AND json_extract(data,'$.connector')=? AND json_extract(data,'$.key')=?",
        (store.project, connector, key),
    ).fetchone()
    if not found:
        return None
    receipt = store.record(db, found[0])["data"]
    if receipt["payload_hash"] != payload_hash:
        raise Conflict(
            "This import event already has different content. Use a new snapshot identity."
        )
    return store.record(db, receipt["target"])


def save(store, db, connector, key, payload, target, *, payload_hash=None):
    digest = payload_hash or sha(encoded(payload).encode())
    existing = lookup(store, db, connector, key, digest)
    if existing:
        return existing
    return store.put(
        db,
        "import_receipt",
        {
            "schema": 1,
            "connector": connector,
            "key": key,
            "payload_hash": digest,
            "original": payload,
            "target": target,
            "witness": "imported_statement",
        },
    )
