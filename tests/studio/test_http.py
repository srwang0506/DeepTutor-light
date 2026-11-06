from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth, studio
from deeptutor.studio.database import Studio


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Studio(tmp_path / "studio", "fixture")
    monkeypatch.setattr(studio, "Studio", lambda: store)
    app = FastAPI()
    app.include_router(studio.router, prefix="/api/studio")
    app.dependency_overrides[auth.require_admin] = lambda: None
    return TestClient(app), store


def test_host_credentials_are_not_available_to_regular_accounts(tmp_path, monkeypatch):
    store = Studio(tmp_path / "studio", "fixture")
    monkeypatch.setattr(studio, "Studio", lambda: store)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    app = FastAPI()
    app.include_router(studio.router, prefix="/api/studio")
    app.dependency_overrides[auth.require_auth] = lambda: SimpleNamespace(
        role="user", user_id="ordinary"
    )
    client = TestClient(app)
    response = client.post(
        "/api/studio/connections",
        json={"title": "server", "provider": "ssh", "alias": "gpu", "roots": ["/project"]},
    )
    assert response.status_code == 403
    assert not store.list("connection")
    assert (
        client.post("/api/studio/connections/unknown/read", json={"operation": "probe"}).status_code
        == 403
    )


def test_connection_schema_rejects_arbitrary_execution_and_secret_fields(client):
    http, store = client
    response = http.post(
        "/api/studio/connections",
        json={
            "title": "server",
            "provider": "ssh",
            "alias": "gpu",
            "roots": ["/project"],
            "private_key": "never stored",
        },
    )
    assert response.status_code == 422
    response = http.post(
        "/api/studio/connections",
        json={"title": "server", "provider": "ssh", "alias": "gpu", "roots": ["/project"]},
    )
    assert response.status_code == 200
    identifier = response.json()["id"]
    response = http.post(
        f"/api/studio/connections/{identifier}/read",
        json={"operation": "exec", "path": "touch /project/file"},
    )
    assert response.status_code == 422
    assert not store.list("remote_snapshot")


def test_manuscript_save_conflict_is_actionable_and_original_survives(client):
    http, store = client
    created = http.post("/api/studio/manuscripts", json={"title": "HTTP fixture"}).json()
    identifier = created["id"]
    original = http.get(
        f"/api/studio/manuscripts/{identifier}/file", params={"path": "main.tex"}
    ).json()
    changed = http.put(
        f"/api/studio/manuscripts/{identifier}/file",
        json={"path": "main.tex", "base_hash": original["hash"], "text": "first edit"},
    )
    assert changed.status_code == 200
    conflict = http.put(
        f"/api/studio/manuscripts/{identifier}/file",
        json={"path": "main.tex", "base_hash": original["hash"], "text": "stale overwrite"},
    )
    assert conflict.status_code == 409
    assert (
        http.get(f"/api/studio/manuscripts/{identifier}/file", params={"path": "main.tex"}).json()[
            "text"
        ]
        == "first edit"
    )
    assert http.get("/api/studio/record/nonexistent").status_code == 404
