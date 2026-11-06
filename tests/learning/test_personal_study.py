from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import dashboard
from deeptutor.services.personal_study import (
    StudyPreferencesConflict,
    StudyPreferencesPatch,
    StudyPreferencesStore,
)
from deeptutor.services.workspace.data_migration import FEATURES, _feature_path


def test_deferral_expires_at_local_midnight_and_keeps_budget(tmp_path):
    store = StudyPreferencesStore(tmp_path)
    now = datetime(2026, 11, 6, 23, 50, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    first = store.read("Asia/Shanghai", now)
    result = store.update(
        StudyPreferencesPatch(
            expected_revision=first.revision,
            minutes=15,
            deferral={"key": "practice:1", "action": "tomorrow"},
        ),
        "Asia/Shanghai",
        now,
    )
    assert result.day_end - now == 600
    assert result.deferred == {"practice:1": result.day_end}
    reopened = StudyPreferencesStore(tmp_path).read("Asia/Shanghai", now + 601)
    assert reopened.deferred == {} and reopened.minutes == 15
    assert [p.name for p in tmp_path.iterdir()] == ["preferences.sqlite3"]


def test_restore_only_changes_the_selected_suggestion(tmp_path):
    store = StudyPreferencesStore(tmp_path)
    for version, key in enumerate(["practice:1", "goal:2"]):
        store.update(
            StudyPreferencesPatch(
                expected_revision=version, deferral={"key": key, "action": "tomorrow"}
            ),
            "UTC",
            1000,
        )
    result = store.update(
        StudyPreferencesPatch(
            expected_revision=2, deferral={"key": "practice:1", "action": "restore"}
        ),
        "UTC",
        1000,
    )
    assert list(result.deferred) == ["goal:2"]


def test_concurrent_tabs_cannot_overwrite_each_other(tmp_path):
    StudyPreferencesStore(tmp_path).read("UTC")

    def save(minutes):
        try:
            return (
                StudyPreferencesStore(tmp_path)
                .update(StudyPreferencesPatch(expected_revision=0, minutes=minutes), "UTC")
                .minutes
            )
        except StudyPreferencesConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(save, [15, 60]))
    assert result.count(None) == 1
    state = StudyPreferencesStore(tmp_path).read("UTC")
    assert state.revision == 1 and state.minutes in result


def test_timezone_is_validated_before_writing_and_dst_is_local(tmp_path):
    root = tmp_path / "new"
    with pytest.raises(ValueError):
        StudyPreferencesStore(root).read("Invalid/Timezone")
    assert not root.exists()
    now = datetime(2026, 11, 1, 0, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    state = StudyPreferencesStore(root).read("America/New_York", now)
    assert state.day_end - now == 25 * 3600


def test_api_validation_and_conflict_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "StudyPreferencesStore", lambda: StudyPreferencesStore(tmp_path))
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    with TestClient(app) as client:
        path = "/api/dashboard/study-preferences?timezone=Asia/Shanghai"
        assert client.get(path).json()["minutes"] == 30
        assert client.patch(path, json={"expected_revision": 0, "minutes": 45}).status_code == 200
        assert client.patch(path, json={"expected_revision": 0, "minutes": 60}).status_code == 409
        assert client.get(path).json()["minutes"] == 45
        for body in [
            {"expected_revision": 1, "minutes": 999},
            {"expected_revision": 1},
            {"expected_revision": 1, "minutes": "15"},
            {"expected_revision": 1, "minutes": 15, "mastery": 1},
        ]:
            assert client.patch(path, json=body).status_code == 422


def test_plan_preferences_are_part_of_workspace_export(tmp_path):
    assert "personal_study" in FEATURES

    class Paths:
        def get_workspace_dir(self):
            return tmp_path

    assert _feature_path(Paths(), "personal_study") == tmp_path / "personal_study"
