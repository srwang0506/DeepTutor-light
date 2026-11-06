"""Project-local planning preferences; learning evidence stays in its existing stores.

Deferring a suggestion only hides it until the next local day. It never changes
Practice due dates, mastery, attempts, or a learning task's completion state.
"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deeptutor.services.path_service import get_path_service
from deeptutor.services.practice.scheduler import day_bounds


class StudyDeferral(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=240, pattern=r"^[a-zA-Z0-9_:.%-]+$")
    action: Literal["tomorrow", "restore"]


class StudyPreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    minutes: Literal[15, 30, 45, 60, 90] | None = None
    deferral: StudyDeferral | None = None

    @model_validator(mode="after")
    def has_change(self):
        if self.minutes is None and self.deferral is None:
            raise ValueError("Choose a time budget or a suggestion to defer.")
        return self


class StudyPreferences(BaseModel):
    revision: int
    minutes: int
    deferred: dict[str, float]
    now: float
    day_end: float
    timezone: str


class StudyPreferencesConflict(ValueError):
    pass


class StudyPreferencesStore:
    def __init__(self, root: Path | None = None):
        self.root = (
            root if root is not None else get_path_service().get_workspace_dir() / "personal_study"
        )

    @contextmanager
    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "preferences.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    minutes INTEGER NOT NULL, revision INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO preferences VALUES (1,30,0);
                CREATE TABLE IF NOT EXISTS deferrals (
                    key TEXT PRIMARY KEY, until_at REAL NOT NULL
                );
            """)
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def snapshot(db, timezone: str, now: float, day_end: float):
        row = db.execute("SELECT * FROM preferences WHERE id=1").fetchone()
        return StudyPreferences(
            revision=row["revision"],
            minutes=row["minutes"],
            now=now,
            day_end=day_end,
            timezone=timezone,
            deferred={
                r["key"]: r["until_at"]
                for r in db.execute(
                    "SELECT * FROM deferrals WHERE until_at > ? ORDER BY key", (now,)
                )
            },
        )

    def read(self, timezone: str, now: float | None = None):
        now = time.time() if now is None else now
        _, end = day_bounds(timezone, now)
        with self.connect() as db:
            db.execute("BEGIN")
            return self.snapshot(db, timezone, now, end)

    def update(self, patch: StudyPreferencesPatch, timezone: str, now: float | None = None):
        now = time.time() if now is None else now
        _, end = day_bounds(timezone, now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM preferences WHERE id=1").fetchone()
            if row["revision"] != patch.expected_revision:
                raise StudyPreferencesConflict(
                    "Your plan changed in another tab. Reload before saving."
                )
            db.execute("DELETE FROM deferrals WHERE until_at <= ?", (now,))
            if patch.minutes is not None:
                db.execute("UPDATE preferences SET minutes=? WHERE id=1", (patch.minutes,))
            if patch.deferral:
                key = patch.deferral.key
                if patch.deferral.action == "restore":
                    db.execute("DELETE FROM deferrals WHERE key=?", (key,))
                else:
                    if (
                        db.execute("SELECT COUNT(*) FROM deferrals").fetchone()[0] >= 200
                        and not db.execute("SELECT 1 FROM deferrals WHERE key=?", (key,)).fetchone()
                    ):
                        raise ValueError("Too many deferred suggestions. Restore one first.")
                    db.execute("INSERT OR REPLACE INTO deferrals VALUES (?,?)", (key, end))
            db.execute("UPDATE preferences SET revision=revision+1 WHERE id=1")
            return self.snapshot(db, timezone, now, end)
