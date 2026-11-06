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
CREATE TABLE IF NOT EXISTS quiz_exposures (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    version TEXT NOT NULL, family TEXT NOT NULL, objective TEXT NOT NULL,
    level TEXT NOT NULL CHECK(level IN ('hint','revealed')), occurred REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS quiz_exposure_identity ON quiz_exposures(version,family,occurred);
CREATE TABLE IF NOT EXISTS quiz_families (
    workspace TEXT NOT NULL, version TEXT NOT NULL, family TEXT NOT NULL,
    objective TEXT NOT NULL, descriptor TEXT NOT NULL, revision INTEGER NOT NULL,
    reviewed REAL NOT NULL, PRIMARY KEY(workspace,version)
);
CREATE TABLE IF NOT EXISTS quiz_family_reviews (
    workspace TEXT NOT NULL, version TEXT NOT NULL, family TEXT NOT NULL,
    objective TEXT NOT NULL, descriptor TEXT NOT NULL, revision INTEGER NOT NULL,
    reviewed REAL NOT NULL, PRIMARY KEY(workspace,version,revision)
);
CREATE TABLE IF NOT EXISTS quiz_migrations (name TEXT PRIMARY KEY, applied REAL NOT NULL);
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

    @staticmethod
    def _exposure_identity(contract):
        metadata = contract.get("metadata") or {}
        # Server contract owns family/objective; never accept these from an answer request.
        family = metadata.get("question_family")
        if (metadata.get("source_scope") or {}).get("studio_object") and metadata.get(
            "question_family_schema"
        ) != 2:
            family = None  # Old source-paragraph hashes did not identify a question family.
        family = family or fingerprint(
            {k: contract.get(k) for k in ("question", "options", "correct_answer")}
        )
        return str(family), str(metadata.get("objective_id") or "")

    def _reviewed_identity(self, conn, session_id, contract):
        row = conn.execute(
            "SELECT family,objective FROM quiz_families WHERE version=? AND workspace=(SELECT COALESCE(json_extract(preferences_json,'$.workspace_id'),'') FROM sessions WHERE id=?)",
            (contract["question_version"], session_id),
        ).fetchone()
        return tuple(row) if row else self._exposure_identity(contract)

    def _migrate_quiz_exposures(self, conn):
        """Preserve pre-ledger assistance without rewriting historical grades."""
        name = "exposure_history_v1"
        if conn.execute("SELECT 1 FROM quiz_migrations WHERE name=?", (name,)).fetchone():
            return
        rows = conn.execute(
            "SELECT q.attempt_id,q.session_id,q.version,q.request_json,q.created_at,c.contract_json "
            "FROM quiz_submissions q JOIN quiz_contracts c ON c.session_id=q.session_id "
            "AND c.turn_id=q.turn_id AND c.question_id=q.question_id AND c.version=q.version"
        ).fetchall()
        for row in rows:
            payload = json.loads(row["request_json"])
            level = payload.get("assistance")
            if level not in {"hint", "revealed"}:
                continue
            family, objective = self._exposure_identity(json.loads(row["contract_json"]))
            conn.execute(
                "INSERT OR IGNORE INTO quiz_exposures VALUES (?,?,?,?,?,?,?)",
                (
                    "legacy:" + row["attempt_id"],
                    row["session_id"],
                    row["version"],
                    family,
                    objective,
                    level,
                    row["created_at"],
                ),
            )
        conn.execute("INSERT INTO quiz_migrations VALUES (?,?)", (name, time.time()))

    async def record_quiz_exposure(self, session_id, contract, level, event_id):
        if level not in {"hint", "revealed"}:
            raise ValueError("Unknown assistance level")

        def write():
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                family, objective = self._reviewed_identity(conn, session_id, contract)
                identity = (session_id, contract["question_version"], family, objective, level)
                old = conn.execute(
                    "SELECT session_id,version,family,objective,level FROM quiz_exposures WHERE id=?",
                    (event_id,),
                ).fetchone()
                if old and (old["session_id"], old["version"], old["level"]) != (
                    identity[0],
                    identity[1],
                    identity[4],
                ):
                    raise ValueError("Exposure event identity already belongs to a different event")
                conn.execute(
                    "INSERT OR IGNORE INTO quiz_exposures VALUES (?,?,?,?,?,?,?)",
                    (
                        event_id,
                        session_id,
                        contract["question_version"],
                        family,
                        objective,
                        level,
                        time.time(),
                    ),
                )
            return {"recorded": True, "level": level}

        return await self._run(write)

    def _quiz_exposure_in_conn(self, conn, session_id, contract, at=None):
        family, objective = self._reviewed_identity(conn, session_id, contract)
        at = time.time() if at is None else at
        row = conn.execute(
            "SELECT e.level,e.occurred FROM quiz_exposures e JOIN sessions s ON s.id=e.session_id LEFT JOIN quiz_families f ON f.version=e.version AND f.workspace=COALESCE(json_extract(s.preferences_json,'$.workspace_id'),'') "
            "WHERE COALESCE(json_extract(s.preferences_json,'$.workspace_id'),'') = "
            "(SELECT COALESCE(json_extract(preferences_json,'$.workspace_id'),'') FROM sessions WHERE id=?) "
            "AND (e.version=? OR (COALESCE(f.family,e.family)=? AND e.occurred>?)) AND e.occurred<=? "
            "ORDER BY (e.level='revealed') DESC,e.occurred DESC LIMIT 1",
            (session_id, contract["question_version"], family, at - 86400, at),
        ).fetchone()
        return dict(row) if row else None

    async def register_quiz_question(self, session_id, turn_id, contract):
        body = {k: v for k, v in contract.items() if k != "question_version"}
        metadata = dict(body.get("metadata") or {})
        if not metadata.get("legacy_reference_delivery"):
            metadata.setdefault("exposure_tracking", 2)
        body["metadata"] = metadata
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
                    **(
                        {"evidence_kind": "unknown", "independent": False}
                        if state == "committed" and receipt.get("exposure_tracking") != 2
                        else {}
                    ),
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
                saved_contract = conn.execute(
                    "SELECT contract_json FROM quiz_contracts WHERE session_id=? AND turn_id=? AND question_id=? AND version=?",
                    (row["session_id"], row["turn_id"], row["question_id"], row["version"]),
                ).fetchone()
                exposure = (
                    self._quiz_exposure_in_conn(
                        conn, row["session_id"], json.loads(saved_contract[0]), row["created_at"]
                    )
                    if saved_contract
                    else None
                )
                declared = json.loads(row["request_json"]).get("assistance")
                if declared in {"hint", "revealed"}:
                    exposure = (
                        {"level": declared} if not exposure or declared == "revealed" else exposure
                    )
                if exposure:
                    attempt["hints_used"] = max(1, attempt.get("hints_used", 0))
                    attempt["assistance"] = exposure["level"]
                    item["hints_used"] = attempt["hints_used"]
                    receipt["evidence_kind"] = (
                        "correction" if exposure["level"] == "revealed" else "assisted"
                    )
                    receipt["independent"] = False
                    receipt["feedback"] = (
                        (receipt.get("feedback") or "")
                        + " 已保留这次修正；此前的答案或提示暴露仍会计入，建议间隔一天后尝试新题检验迁移。"
                    )
                else:
                    contract = json.loads(saved_contract[0]) if saved_contract else {}
                    metadata = contract.get("metadata") or {}
                    tracked = metadata.get("exposure_tracking") == 2
                    receipt["evidence_kind"] = "independent" if tracked else "unknown"
                    receipt["independent"] = tracked
                    family, objective = self._reviewed_identity(conn, row["session_id"], contract)
                    # Only a reviewed objective supports a transfer claim. An edition or
                    # paragraph identifier alone does not establish conceptual equivalence.
                    reviewed_family = conn.execute(
                        "SELECT 1 FROM quiz_families WHERE version=? AND workspace=(SELECT COALESCE(json_extract(preferences_json,'$.workspace_id'),'') FROM sessions WHERE id=?)",
                        (contract["question_version"], row["session_id"]),
                    ).fetchone()
                    if (
                        tracked
                        and objective
                        and (reviewed_family or metadata.get("transfer_objective_verified"))
                    ):
                        prior = conn.execute(
                            "SELECT MAX(e.occurred) FROM quiz_exposures e JOIN sessions s ON s.id=e.session_id LEFT JOIN quiz_families f ON f.version=e.version AND f.workspace=COALESCE(json_extract(s.preferences_json,'$.workspace_id'),'') "
                            "WHERE COALESCE(f.objective,e.objective)=? AND COALESCE(f.family,e.family)!=? AND e.occurred<? AND "
                            "COALESCE(json_extract(s.preferences_json,'$.workspace_id'),'')="
                            "(SELECT COALESCE(json_extract(preferences_json,'$.workspace_id'),'') FROM sessions WHERE id=?)",
                            (objective, family, row["created_at"], row["session_id"]),
                        ).fetchone()[0]
                        if prior is not None and row["created_at"] - prior >= 86400:
                            receipt["evidence_kind"] = "delayed_transfer"
                if json.loads(row["request_json"]).get("offline"):
                    receipt["independent"] = False
                    if not exposure:
                        receipt["evidence_kind"] = "unknown"
                    receipt["feedback"] = (
                        receipt.get("feedback") or ""
                    ) + " 离线作答：独立性未核实，未计为独立掌握或延迟迁移证据。"
                receipt["exposure_tracking"] = 2
                attempt["evidence_kind"] = receipt["evidence_kind"]
                attempt["independent"] = receipt["independent"]
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
                if saved_contract and receipt.get("feedback") and committed:
                    # Classify first, then atomically record the solution shown with this receipt.
                    # Replaying a receipt cannot reset an earlier disclosure timestamp.
                    contract = json.loads(saved_contract[0])
                    family, objective = self._reviewed_identity(conn, row["session_id"], contract)
                    conn.execute(
                        "INSERT OR IGNORE INTO quiz_exposures VALUES (?,?,?,?,?,?,?)",
                        (
                            "feedback:" + attempt_id,
                            row["session_id"],
                            row["version"],
                            family,
                            objective,
                            "revealed",
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

    async def quiz_progress(self, session_id, turn_id):
        def read():
            with self._connect() as conn:
                total = conn.execute(
                    "SELECT COUNT(DISTINCT question_id) FROM quiz_contracts WHERE session_id=? AND turn_id=?",
                    (session_id, turn_id),
                ).fetchone()[0]
                completed = conn.execute(
                    "SELECT COUNT(DISTINCT question_id) FROM quiz_submissions WHERE session_id=? AND turn_id=? AND state='committed'",
                    (session_id, turn_id),
                ).fetchone()[0]
                return {
                    "total": total,
                    "answered": completed,
                    "complete": total > 0 and completed == total,
                }

        return await self._run(read)

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
