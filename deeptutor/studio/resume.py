"""Small project-local continuation pointers; activity is not mastery evidence."""

from .actions import resolve
from .contracts import ObjectRef


def save(store, ref: ObjectRef):
    row = resolve(store, ref)
    if row["kind"] != "edition" or not ref.locator.page:
        raise ValueError("Choose a source page to resume.")
    from .catalog import page_source

    loc = ref.locator
    if not loc.blob_hash or not any(
        f["hash"] == loc.blob_hash for f in row["data"].get("files", [])
    ):
        raise ValueError("Choose a file belonging to this source revision.")
    original = page_source(store, ref.id, loc.blob_hash, loc.page, loc.region)
    if loc.quote and " ".join(loc.quote.split()) not in " ".join(original["text"].split()):
        raise ValueError("Selection does not match the original page.")
    with store.db(write=True) as db:
        old = db.execute(
            "SELECT id FROM records WHERE scope=? AND kind='resume' AND json_extract(data,'$.source.id')=?",
            (store.project, ref.id),
        ).fetchone()
        prior = store.record(db, old[0]) if old else None
        title = store.record(db, row["data"]["work_id"], "work")["data"]["title"]
        return store.put(
            db,
            "resume",
            {"title": title, "source": ref.model_dump()},
            identifier=prior["id"] if prior else None,
            expected=prior["revision"] if prior else None,
        )


def pending(store):
    rows = []
    with store.db() as db:
        snapshots = db.execute(
            "SELECT r.id FROM records r WHERE r.scope=? AND r.kind='remote_snapshot' AND json_extract(r.data,'$.request.operation')='read' AND NOT EXISTS(SELECT 1 FROM dependencies d WHERE d.source=r.id AND d.relation='imported_log') ORDER BY r.updated DESC LIMIT 3",
            (store.project,),
        ).fetchall()
        for item in snapshots:
            row = store.record(db, item[0])
            rows.append(
                {
                    "id": row["id"],
                    "kind": "remote_snapshot",
                    "revision": row["revision"],
                    "updated": row["updated"],
                    "title": row["data"].get("request", {}).get("path", "读取快照"),
                }
            )

    for kind, condition in [
        ("resume", "1"),
        (
            "evidence",
            "json_extract(data,'$.verification')!='confirmed' OR COALESCE(json_extract(data,'$.review_schema'),0)<2",
        ),
        ("claim", "json_extract(data,'$.status')!='confirmed'"),
        ("proposal", "json_extract(data,'$.status')='pending'"),
        ("activity", "json_extract(data,'$.state')='committed'"),
        ("run", "json_extract(data,'$.status') IN ('failed','planned')"),
    ]:
        with store.db() as db:
            for item in db.execute(
                "SELECT id FROM records WHERE scope=? AND kind=? AND ("
                + condition
                + ") ORDER BY updated DESC LIMIT "
                + ("500" if kind == "activity" else "3"),
                (store.project, kind),
            ):
                row = store.record(db, item[0])
                rows.append(
                    {
                        "id": row["id"],
                        "kind": kind,
                        "revision": row["revision"],
                        "updated": row["updated"],
                        "title": row["data"].get("title")
                        or row["data"].get("label")
                        or row["data"].get("path")
                        or kind,
                        "source": row["data"].get("source"),
                        "manuscript_id": row["data"].get("manuscript_id"),
                        "session_id": row["data"].get("session_id"),
                        "turn_id": row["data"].get("turn_id"),
                    }
                )
    return sorted(rows, key=lambda r: r["updated"], reverse=True)
