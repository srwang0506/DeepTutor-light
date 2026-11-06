"""Quiz contract and submission storage, owned by the existing SessionStore.

No provider runs inside a SQLite transaction. A lease fences late results;
cancellation and the immutable assessment commit use the same transaction.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS quiz_contracts (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL, question_id TEXT NOT NULL, version TEXT NOT NULL,
    contract_json TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY(session_id, turn_id, question_id, version)
);
CREATE TABLE IF NOT EXISTS quiz_projection_outbox (
    attempt_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_submissions (
    attempt_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL, question_id TEXT NOT NULL, version TEXT NOT NULL,
    request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
    state TEXT NOT NULL, lease TEXT NOT NULL, deadline REAL NOT NULL,
    receipt_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
"""


class QuizContractStore:
    """Mixin using SessionStore's connection, lock, and assessment transaction."""

    async def register_quiz_question(self, session_id, turn_id, contract):
        body = {k: v for k, v in contract.items() if k != "question_version"}
        version = fingerprint(body)
        body["question_version"] = version

        def write():
            with self._connect() as conn:
                turn = conn.execute(
                    "SELECT session_id FROM turns WHERE id = ?", (turn_id,)
                ).fetchone()
                if turn is None or turn["session_id"] != session_id:
                    raise ValueError("Question generation turn does not belong to this session")
                conn.execute(
                    "INSERT OR IGNORE INTO quiz_contracts VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        turn_id,
                        body["question_id"],
                        version,
                        json.dumps(body, ensure_ascii=False),
                        time.time(),
                    ),
                )
            return body

        return await self._run(write)

    async def get_quiz_question(self, session_id, turn_id, question_id, version=""):
        def read():
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT contract_json FROM quiz_contracts WHERE session_id=? AND turn_id=? AND question_id=? "
                    + ("AND version=?" if version else "ORDER BY created_at DESC LIMIT 1"),
                    (session_id, turn_id, question_id, version)
                    if version
                    else (session_id, turn_id, question_id),
                ).fetchone()
            return json.loads(rows[0]) if rows else None

        return await self._run(read)

    async def quiz_submission(self, attempt_id, session_id):
        def read():
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM quiz_submissions WHERE attempt_id=? AND session_id=?",
                    (attempt_id, session_id),
                ).fetchone()
                if not row:
                    return None
                state = row["state"]
                receipt = json.loads(row["receipt_json"])
                if state == "grading" and row["deadline"] < time.time():
                    state = "ungraded"
                    receipt = {
                        "error": "Grading was interrupted. Your answer is saved; retry this submission."
                    }
                return {
                    **receipt,
                    "attempt_id": attempt_id,
                    "state": state,
                    "assessment_committed": state == "committed",
                }

        return await self._run(read)

    async def begin_quiz_submission(self, payload, *, retry=False):
        body = {k: v for k, v in payload.items() if k != "retry"}
        digest = fingerprint(body)
        lease = uuid.uuid4().hex

        def begin():
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                old = conn.execute(
                    "SELECT * FROM quiz_submissions WHERE attempt_id=?", (body["attempt_id"],)
                ).fetchone()
                if old:
                    if old["request_hash"] != digest:
                        raise ValueError("Attempt identity already belongs to a different answer")
                    interrupted = old["state"] == "grading" and old["deadline"] < time.time()
                    if not retry or not (old["state"] == "ungraded" or interrupted):
                        return None
                    conn.execute(
                        "UPDATE quiz_submissions SET state='grading', lease=?, deadline=?, receipt_json='{}' WHERE attempt_id=?",
                        (lease, time.time() + 180, body["attempt_id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO quiz_submissions (attempt_id, session_id, turn_id, question_id, version, request_hash, request_json, state, lease, deadline, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'grading', ?, ?, ?)",
                        (
                            body["attempt_id"],
                            body["session_id"],
                            body["turn_id"],
                            body["question_id"],
                            body["question_version"],
                            digest,
                            json.dumps(body),
                            lease,
                            time.time() + 180,
                            time.time(),
                        ),
                    )
                return lease

        return await self._run(begin)

    async def finish_quiz_submission(self, attempt_id, lease, item, attempt, receipt):
        def finish():
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM quiz_submissions WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
                if not row or row["state"] != "grading" or row["lease"] != lease:
                    return False
                committed = receipt.get("result") in {"correct", "partial", "incorrect"}
                if committed:
                    # The verdict is authoritative here. Notebook projection is
                    # independently recoverable and cannot roll it back.
                    attempt["occurred_at"] = row["created_at"]
                    attempt["created_at"] = row["created_at"]
                    receipt["projection_status"] = "pending"
                    conn.execute(
                        "INSERT OR REPLACE INTO quiz_projection_outbox VALUES (?, ?, ?)",
                        (
                            attempt_id,
                            json.dumps(
                                {"session_id": row["session_id"], "item": item, "attempt": attempt}
                            ),
                            time.time(),
                        ),
                    )
                conn.execute(
                    "UPDATE quiz_submissions SET state=?, receipt_json=? WHERE attempt_id=?",
                    ("committed" if committed else "ungraded", json.dumps(receipt), attempt_id),
                )
                return True

        committed = await self._run(finish)
        if committed:
            await self.reconcile_quiz_projections(attempt_id=attempt_id)
        return committed

    async def cancel_quiz_submission(self, attempt_id, session_id):
        def cancel():
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                # A committed grade is irreversible here. Cancelling explanation
                # or an old worker cannot roll back learning evidence.
                conn.execute(
                    "UPDATE quiz_submissions SET state='cancelled', lease='' WHERE attempt_id=? AND session_id=? AND state IN ('grading', 'ungraded')",
                    (attempt_id, session_id),
                )

        await self._run(cancel)
        return await self.quiz_submission(attempt_id, session_id)

    async def reconcile_quiz_projections(self, *, attempt_id=None, limit=100):
        def recover():
            recovered = 0
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT * FROM quiz_projection_outbox "
                    + ("WHERE attempt_id=? " if attempt_id else "")
                    + "ORDER BY created_at LIMIT ?",
                    (attempt_id, limit) if attempt_id else (limit,),
                ).fetchall()
                for row in rows:
                    data = json.loads(row["payload_json"])
                    conn.execute("SAVEPOINT quiz_projection")
                    try:
                        entry_id, _, _ = self._record_assessment_in_conn(
                            conn, data["session_id"], data["item"], data["attempt"]
                        )
                    except Exception:
                        conn.execute("ROLLBACK TO quiz_projection")
                    else:
                        stored = conn.execute(
                            "SELECT receipt_json FROM quiz_submissions WHERE attempt_id=?",
                            (row["attempt_id"],),
                        ).fetchone()
                        receipt = json.loads(stored[0]) | {
                            "projection_status": "complete",
                            "entry_id": entry_id,
                        }
                        conn.execute(
                            "UPDATE quiz_submissions SET receipt_json=? WHERE attempt_id=?",
                            (json.dumps(receipt), row["attempt_id"]),
                        )
                        conn.execute(
                            "DELETE FROM quiz_projection_outbox WHERE attempt_id=?",
                            (row["attempt_id"],),
                        )
                        recovered += 1
                    finally:
                        conn.execute("RELEASE quiz_projection")
            return recovered

        return await self._run(recover)
