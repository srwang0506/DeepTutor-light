"""Memory snapshots stay within the chosen project; recall has a separate policy."""

from pathlib import Path
import sqlite3

import pytest

from deeptutor.services.memory.snapshot import adapters
from deeptutor.services.path_service import PathService


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            capability TEXT,
            created_at REAL
        );
        CREATE TABLE notebook_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            turn_id TEXT,
            question_id TEXT,
            question TEXT,
            question_type TEXT,
            options_json TEXT,
            correct_answer TEXT,
            explanation TEXT,
            difficulty TEXT,
            user_answer TEXT,
            is_correct INTEGER,
            bookmarked INTEGER,
            created_at REAL
        );
        """
    )
    return conn


def _add_session(
    conn: sqlite3.Connection, sid: str, title: str, *, question: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, 1000.0, 1200.0)",
        (sid, title),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, capability, created_at) "
        "VALUES (?, 'user', ?, 'chat', 1100.0)",
        (sid, f"about {title}"),
    )
    if question is not None:
        conn.execute(
            "INSERT INTO notebook_entries (session_id, turn_id, question_id, question, "
            "question_type, correct_answer, user_answer, is_correct, bookmarked, created_at) "
            "VALUES (?, 't1', 'q1', ?, 'choice', 'A', 'B', 0, 0, 1150.0)",
            (sid, question),
        )
    # The adapters read through a *separate* read-only connection; uncommitted
    # rows would be invisible to it.
    conn.commit()


@pytest.mark.parametrize("surface", ["chat", "quiz"])
def test_snapshot_and_probe_only_read_the_active_project(tmp_path, monkeypatch, surface):
    paths = [PathService(tmp_path / name) for name in ("general", "project")]
    for i, path in enumerate(paths):
        db = path.get_chat_history_db()
        db.parent.mkdir(parents=True, exist_ok=True)
        with _make_db(db) as conn:
            _add_session(conn, f"s{i}", f"Project {i}", question="BFS queue")
    for i, path in enumerate(paths):
        monkeypatch.setattr(adapters, "get_path_service", lambda: path)
        entities = (
            adapters.read_chat_entities() if surface == "chat" else adapters.read_quiz_entities()
        )
        assert entities
        assert all(e.metadata.get("session_id") == f"s{i}" for e in entities)
        assert adapters._iter_chat_dbs() == [("", path.get_chat_history_db())]
