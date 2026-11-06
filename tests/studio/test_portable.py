import json

import pytest

from deeptutor.studio import portable as p
from deeptutor.studio.database import Conflict, Studio

PASSWORD = "a sufficiently long passphrase"


def record(store, kind="claim", **data):
    with store.db(write=True) as db:
        return store.put(db, kind, data or {"text": "first"})


@pytest.fixture
def devices(tmp_path):
    return Studio(tmp_path / "one", project="a"), Studio(tmp_path / "two", project="b")


def transfer(a, b):
    raw = p.export(a, PASSWORD)
    preview = p.preview(b, raw, PASSWORD)
    return p.apply(b, preview["batch_id"], {}), raw


def test_encryption_history_and_replay(devices):
    a, b = devices
    old = record(a)
    with a.db(write=True) as db:
        a.put(db, "claim", {"text": "second"}, identifier=old["id"], expected=1)
        target = a.put(db, "review", {"title": "snapshot"})
        a.link(db, old["id"], target["id"], source_revision=1)
    result, raw = transfer(a, b)
    assert b.get(old["id"])["data"]["text"] == "second"
    assert result["unresolved_historical_edges"] == []
    with b.db() as db:
        edge = db.execute("SELECT source_revision FROM dependencies").fetchone()
        assert (
            json.loads(
                db.execute(
                    "SELECT data FROM revisions WHERE id=? AND revision=?", (old["id"], edge[0])
                ).fetchone()[0]
            )["text"]
            == "first"
        )
    assert p.preview(b, raw, PASSWORD)["state"] == "complete"
    assert p.apply(b, result["batch_id"], {}) == result
    with pytest.raises(ValueError, match="passphrase"):
        p.unseal(raw, "another wrong passphrase")


def test_conflict_explicit_choice_and_stale_preview(devices):
    a, b = devices
    item = record(a)
    transfer(a, b)
    for store, text in [(a, "remote"), (b, "local")]:
        old = store.get(item["id"])
        with store.db(write=True) as db:
            store.put(db, "claim", {"text": text}, identifier=item["id"], expected=old["revision"])
    raw = p.export(a, PASSWORD)
    view = p.preview(b, raw, PASSWORD)
    key = "object/" + item["id"]
    assert view["items"][0]["state"] == "conflict"
    with pytest.raises(Conflict, match="every conflict"):
        p.apply(b, view["batch_id"], {})
    p.apply(b, view["batch_id"], {key: "keep"})
    assert b.get(item["id"])["data"]["text"] == "local"
    # A later remote update should remain a conflict after keeping the local edit.
    old = a.get(item["id"])
    with a.db(write=True) as db:
        a.put(db, "claim", {"text": "remote2"}, identifier=item["id"], expected=old["revision"])
    view = p.preview(b, p.export(a, PASSWORD), PASSWORD)
    with b.db(write=True) as db:
        b.put(
            db,
            "claim",
            {"text": "local2"},
            identifier=item["id"],
            expected=b.get(item["id"])["revision"],
        )
    with pytest.raises(Conflict, match="changed after preview"):
        p.apply(b, view["batch_id"], {key: "incoming"})


def test_old_package_cannot_roll_back_newer_data(devices):
    a, b = devices
    item = record(a)
    old = p.export(a, PASSWORD)
    with a.db(write=True) as db:
        a.put(db, "claim", {"text": "new"}, identifier=item["id"], expected=1)
    transfer(a, b)
    with pytest.raises(Conflict, match="predates"):
        p.preview(b, old, PASSWORD)


def test_cross_workspace_identity_is_not_overwritten(devices):
    a, b = devices
    item = record(a)
    raw = p.export(a, PASSWORD)
    transfer(a, b)
    other = Studio(b.root, project="different")
    with pytest.raises((ValueError, LookupError)):
        p.preview(other, raw, PASSWORD)
    assert b.get(item["id"])["data"]["text"] == "first"


def test_bad_hash_and_escaped_edge_rejected_before_mutation(devices):
    a, b = devices
    record(a)
    package = p.collect(a)
    key = next(iter(package["resources"]))
    package["resources"][key]["history"] = {"bad": {"text": "evil"}}
    with pytest.raises(ValueError, match="checksum"):
        p.preview(b, p.seal(package, PASSWORD), PASSWORD)
    assert b.list("claim") == []


@pytest.mark.asyncio
async def test_conversation_ids_and_receipts_roundtrip(devices, tmp_path):
    from deeptutor.learning.quiz import QuizSubmission, submit_quiz
    from deeptutor.services.session.sqlite_store import SQLiteSessionStore

    a, b = devices
    left = SQLiteSessionStore(db_path=tmp_path / "left.db")
    right = SQLiteSessionStore(db_path=tmp_path / "right.db")
    await left.create_session(title="Study", session_id="chat")
    await left.begin_turn("chat", turn_id="turn")
    question = await left.register_quiz_question(
        "chat",
        "turn",
        {
            "question_id": "q",
            "question": "2+2",
            "question_type": "choice",
            "options": {"A": "4"},
            "correct_answer": "A",
            "metadata": {},
        },
    )
    await submit_quiz(
        left,
        QuizSubmission(
            session_id="chat",
            turn_id="turn",
            question_id="q",
            question_version=question["question_version"],
            attempt_id="portable-attempt-123456",
            answer="A",
        ),
    )
    with left._connect() as db:
        db.execute(
            "UPDATE sessions SET preferences_json=? WHERE id='chat'", ('{"workspace_id":"a"}',)
        )
    # Destination integer IDs differ; a receipt must still open its own answer.
    await right.create_session(title="Existing", session_id="other")
    with left._connect() as db:
        answer = dict(
            db.execute("SELECT * FROM notebook_entries WHERE session_id='chat'").fetchone()
        )
        db.execute("INSERT INTO notebook_categories VALUES (1,'Algebra',1)")
        db.execute("INSERT INTO notebook_entry_categories VALUES (?,1)", (answer["id"],))
        family = (
            "a",
            question["question_version"],
            "family-arithmetic",
            "addition",
            '{"objective":"Addition","method":"arithmetic","conditions":"integers","rationale":"Reviewed original solution"}',
            1,
            1.0,
        )
        db.execute("INSERT INTO quiz_families VALUES (?,?,?,?,?,?,?)", family)
        db.execute("INSERT INTO quiz_family_reviews VALUES (?,?,?,?,?,?,?)", family)
    with right._connect() as db:
        existing = {
            **answer,
            "id": 100,
            "session_id": "other",
            "origin_ref": "other",
            "question_id": "existing",
        }
        db.execute(
            f"INSERT INTO notebook_entries ({','.join(existing)}) VALUES ({','.join('?' for _ in existing)})",
            list(existing.values()),
        )
    view = p.preview(b, p.export(a, PASSWORD, sessions=left), PASSWORD, sessions=right)
    p.apply(b, view["batch_id"], {}, sessions=right)
    saved = await right.quiz_submission("portable-attempt-123456", "chat")
    assert saved["result"] == "correct"
    assert saved["entry_id"] != answer["id"]
    with right._connect() as db:
        assert (
            db.execute(
                "SELECT session_id FROM notebook_entries WHERE id=?", (saved["entry_id"],)
            ).fetchone()[0]
            == "chat"
        )
        assert (
            db.execute(
                "SELECT c.name FROM notebook_categories c JOIN notebook_entry_categories n ON n.category_id=c.id WHERE n.entry_id=?",
                (saved["entry_id"],),
            ).fetchone()[0]
            == "Algebra"
        )
        assert (
            db.execute(
                "SELECT family FROM quiz_families WHERE workspace='b' AND version=?",
                (question["question_version"],),
            ).fetchone()[0]
            == "family-arithmetic"
        )

    assert len(await right.list_assessment_attempts("chat")) == 1
    again = p.preview(b, p.export(a, PASSWORD, sessions=left), PASSWORD, sessions=right)
    assert all(item["state"] == "unchanged" for item in again["items"])


@pytest.mark.parametrize(
    "bad", ["wrong", [{}], {"sessions": [None]}, {"sessions": [{"id": "x"}], "messages": [{}]}]
)
def test_malformed_sessions_are_rejected_before_writes(devices, bad):
    a, b = devices
    payload = p.collect(a)
    payload["resources"]["session/x"] = bad
    with pytest.raises(ValueError):
        p.preview(b, p.seal(payload, PASSWORD), PASSWORD)


def test_repreview_recovers_unapplied_step_and_keeps_local_lineage(devices):
    a, b = devices
    source = record(a)
    target = record(a, "review", title="remote")
    with a.db(write=True) as db:
        a.link(db, source["id"], target["id"], "basis")
    transfer(a, b)
    local_source = record(b, text="local source")
    with b.db(write=True) as db:
        old = b.record(db, target["id"])
        b.put(db, "review", {"title": "local"}, identifier=target["id"], expected=old["revision"])
        b.link(db, local_source["id"], target["id"], "basis")
    with a.db(write=True) as db:
        a.put(
            db,
            "review",
            {"title": "remote next"},
            identifier=target["id"],
            expected=target["revision"],
        )
    raw = p.export(a, PASSWORD)
    view = p.preview(b, raw, PASSWORD)
    key = "object/" + target["id"]
    with b.db(write=True) as db:
        db.execute(
            "INSERT INTO portable_steps VALUES (?,?,?,?,?,?)",
            (view["batch_id"], key, "incoming", "stale", None, "prepared"),
        )
    view = p.preview(b, raw, PASSWORD)
    p.apply(b, view["batch_id"], {key: "keep"})
    with b.db() as db:
        assert db.execute(
            "SELECT 1 FROM dependencies WHERE source=? AND target=?",
            (local_source["id"], target["id"]),
        ).fetchone()
    assert b.get(target["id"])["data"]["title"] == "local"


@pytest.mark.asyncio
async def test_pending_grade_projection_survives_transfer(devices, tmp_path, monkeypatch):
    from deeptutor.learning.quiz import QuizSubmission, submit_quiz
    from deeptutor.services.session.sqlite_store import SQLiteSessionStore

    a, b = devices
    left = SQLiteSessionStore(db_path=tmp_path / "pending-left.db")
    right = SQLiteSessionStore(db_path=tmp_path / "pending-right.db")
    await left.create_session(title="Pending", session_id="pending-chat")
    await left.begin_turn("pending-chat", turn_id="pending-turn")
    q = await left.register_quiz_question(
        "pending-chat",
        "pending-turn",
        {
            "question_id": "q",
            "question": "2 + 2?",
            "question_type": "choice",
            "options": {"A": "4"},
            "correct_answer": "A",
            "metadata": {},
        },
    )
    with left._connect() as db:
        db.execute(
            "UPDATE sessions SET preferences_json=? WHERE id='pending-chat'",
            ('{"workspace_id":"a"}',),
        )

    def unavailable(*args):
        raise RuntimeError("Notebook unavailable during grade commit")

    monkeypatch.setattr(left, "_upsert_notebook_entries_in_conn", unavailable)
    receipt = await submit_quiz(
        left,
        QuizSubmission(
            session_id="pending-chat",
            turn_id="pending-turn",
            question_id="q",
            question_version=q["question_version"],
            attempt_id="pending-portable-attempt",
            answer="A",
        ),
    )
    assert receipt["assessment_committed"] and receipt["projection_status"] == "pending"
    raw = p.export(a, PASSWORD, sessions=left)
    view = p.preview(b, raw, PASSWORD, sessions=right)
    p.apply(b, view["batch_id"], {}, sessions=right)
    assert await right.reconcile_quiz_projections() == 1
    assert await right.reconcile_quiz_projections() == 0
    recovered = await right.quiz_submission("pending-portable-attempt", "pending-chat")
    assert recovered["result"] == "correct" and recovered["projection_status"] == "complete"
    assert len(await right.list_assessment_attempts("pending-chat")) == 1
    # Replaying the package cannot reset the recovered receipt.
    assert p.preview(b, raw, PASSWORD, sessions=right)["state"] == "complete"
    assert (await right.quiz_submission("pending-portable-attempt", "pending-chat"))[
        "projection_status"
    ] == "complete"
    # A second writer changing the destination after preview is rejected in the
    # same transaction that would install the incoming conversation.
    expected = p.digest(p.local_resource(b, "session/pending-chat", sessions=right))
    with right._connect() as db:
        db.execute("UPDATE sessions SET title='Newer local title' WHERE id='pending-chat'")
    incoming = p.unseal(raw, PASSWORD)["resources"]["session/pending-chat"]
    with pytest.raises(Conflict, match="Conversation changed"):
        p.install_session(right, "pending-chat", incoming, "b", expected)


def test_sqlite_export_uses_same_committed_snapshot_for_conflict_checks(
    devices, tmp_path, monkeypatch
):
    import sqlite3

    from deeptutor.services.workspace import data_migration

    a, _ = devices
    root = tmp_path / "feature"
    root.mkdir()
    path = root / "state.sqlite3"
    monkeypatch.setattr(p, "FEATURES", {"learning"})
    monkeypatch.setattr(data_migration, "_feature_path", lambda *_: root)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
        db.execute("INSERT INTO notes VALUES (1,'first')")
        db.commit()
        raw = p.export(a, PASSWORD, paths=object())
        view = p.preview(a, raw, PASSWORD, paths=object())
        assert [r["state"] for r in view["items"]] == ["unchanged"]
        db.execute("UPDATE notes SET text='committed to WAL'")
        db.commit()
        view = p.preview(a, raw, PASSWORD, paths=object())
        assert view["items"][0]["state"] == "conflict"
