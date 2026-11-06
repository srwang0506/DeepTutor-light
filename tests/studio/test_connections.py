import base64
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from deeptutor.studio import connections, remote_reader
from deeptutor.studio.connections import ConnectionInput, ReadRequest, bounded_process
from deeptutor.studio.database import Missing, Studio, sha
from deeptutor.studio.models import RunInput


@pytest.fixture
def remote(tmp_path):
    root = tmp_path / "server" / "project"
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "logs" / "train.log").write_text("epoch 1 mAP 23.4\n")
    (root / "manifest.json").write_text('{"seed": 42}')
    (root / ".env").write_text("secret fixture")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "manifest.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return root


def local_transport(config, request):
    code, raw, _ = bounded_process(
        [sys.executable, "-I", remote_reader.__file__],
        json.dumps({**request.model_dump(), "roots": config["roots"]}).encode(),
    )
    assert code == 0
    response = json.loads(raw)
    if not response["ok"]:
        raise ValueError(response["error"])
    return response["result"]


def setup_connection(tmp_path, remote):
    store = Studio(tmp_path / "studio", "project-a")
    connection = connections.create_connection(
        store,
        ConnectionInput(
            title="Simulated server", provider="ssh", alias="test-server", roots=[str(remote)]
        ),
    )
    return store, connection


def test_protocol_runs_separately_and_import_preserves_bytes(tmp_path, remote):
    store, connection = setup_connection(tmp_path, remote)

    def read(command):
        return connections.read_connection(
            store, connection["id"], command, transport=local_transport
        )

    probe = read(ReadRequest(operation="probe"))
    assert probe["data"]["result"]["mode"] == "read_only"
    listing = read(ReadRequest(operation="list", path=str(remote)))
    assert ".env" not in [x["name"] for x in listing["data"]["result"]["entries"]]
    repo = read(ReadRequest(operation="repository", path=str(remote)))
    assert len(repo["data"]["result"]["commit"]) == 40
    assert repo["data"]["result"]["analysis"] == "static_only"
    log = read(ReadRequest(operation="read", path=str(remote / "logs/train.log")))
    source = connections.snapshot_text(store, log["id"])
    assert source["hash"] == sha(b"epoch 1 mAP 23.4\n")
    run = connections.import_snapshot_run(
        store, log["id"], RunInput(title="Imported log", status="planned"), repo["id"]
    )
    assert run["data"]["status"] == "planned"
    assert run["data"]["execution_verified"] is False
    assert run["data"]["commit"] == ""
    assert (
        run["data"]["remote_source"]["repository_observed_commit"]
        == repo["data"]["result"]["commit"]
    )
    (remote / "logs/train.log").write_text("new log")
    assert connections.snapshot_text(store, log["id"])["text"] == "epoch 1 mAP 23.4\n"
    assert store.impact(log["id"])[0]["id"] == run["id"]


@pytest.mark.parametrize(
    "name", ["../outside.txt", ".env", ".env.local", "id_ed25519", "secret.pem"]
)
def test_scope_and_protected_files_rejected(tmp_path, remote, name):
    path = remote / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private fixture")
    with pytest.raises(ValueError):
        local_transport({"roots": [str(remote)]}, ReadRequest(operation="read", path=str(path)))


def test_symlink_and_special_files_cannot_escape(tmp_path, remote):
    outside = tmp_path / "outside.txt"
    outside.write_text("not in scope")
    (remote / "escape.log").symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        local_transport(
            {"roots": [str(remote)]}, ReadRequest(operation="read", path=str(remote / "escape.log"))
        )
    import os

    os.mkfifo(remote / "pipe.log")
    with pytest.raises(ValueError, match="regular"):
        local_transport(
            {"roots": [str(remote)]}, ReadRequest(operation="read", path=str(remote / "pipe.log"))
        )


def test_large_file_rejected(tmp_path, remote):
    (remote / "big.log").write_bytes(b"x" * (remote_reader.MAX_FILE + 1))
    with pytest.raises(ValueError, match="512"):
        local_transport(
            {"roots": [str(remote)]}, ReadRequest(operation="read", path=str(remote / "big.log"))
        )


def test_failed_read_is_retryable_and_audited(tmp_path, remote):
    store, connection = setup_connection(tmp_path, remote)

    def disconnected(*args):
        raise TimeoutError("fixture disconnect")

    with pytest.raises(ValueError, match="disconnect"):
        connections.read_connection(
            store, connection["id"], ReadRequest(operation="probe"), transport=disconnected
        )
    assert store.list("connection_event")[0]["data"]["status"] == "failed"
    assert not store.list("remote_snapshot")
    result = connections.read_connection(
        store, connection["id"], ReadRequest(operation="probe"), transport=local_transport
    )
    assert result["data"]["status"] == "succeeded"


def test_connection_change_mid_read_cannot_store_snapshot(tmp_path, remote):
    store, connection = setup_connection(tmp_path, remote)

    def revoked(config, request):
        connections.create_connection(
            store, ConnectionInput(**{**config, "enabled": False}), connection["id"], 1
        )
        return {"mode": "read_only"}

    with pytest.raises(ValueError, match="scope changed"):
        connections.read_connection(
            store, connection["id"], ReadRequest(operation="probe"), transport=revoked
        )
    assert not store.list("remote_snapshot")


def test_project_isolation_and_shell_injection(tmp_path, remote):
    store, connection = setup_connection(tmp_path, remote)
    with pytest.raises(Missing):
        Studio(store.root, "project-b").get(connection["id"])
    for alias in ["-oProxyCommand=x", "host;pwd", "x$(pwd)", "x y", "user@host"]:
        with pytest.raises(ValueError):
            ConnectionInput(title="test", provider="ssh", alias=alias, roots=[str(remote)])


def test_bounded_process_timeout_and_output():
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        bounded_process([sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.1)
    assert time.monotonic() - start < 2
    with pytest.raises(ValueError, match="size limit"):
        bounded_process([sys.executable, "-c", 'print("x" * 10000)'], limit=100)


def test_github_uses_fixed_commit_and_never_raw_download_url(monkeypatch):
    calls = []
    commit = "a" * 40

    def api(endpoint):
        calls.append(endpoint)
        if "/git/commits/" in endpoint:
            return {"tree": {"sha": "b" * 40}}
        if "/commits/" in endpoint:
            return {"sha": commit}
        if "/git/trees/" in endpoint:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "mode": "100644",
                        "sha": "c" * 40,
                        "size": 6,
                    }
                ]
            }
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(b"readme").decode(),
        }

    monkeypatch.setattr(connections, "github_api", api)
    response = connections.github_read(
        {"repository": "owner/repo", "ref": "main"}, ReadRequest(operation="read", path="README.md")
    )
    assert response["commit"] == commit
    assert calls[0] == "repos/owner/repo/commits/main"
    assert calls[1].endswith(commit)
    assert calls[-1] == "repos/owner/repo/git/blobs/" + "c" * 40
    assert not any("/contents/" in c for c in calls)


def test_invalid_remote_digest_never_imported(tmp_path, remote):
    store, connection = setup_connection(tmp_path, remote)
    with pytest.raises(ValueError, match="integrity"):
        connections.read_connection(
            store,
            connection["id"],
            ReadRequest(operation="read", path=str(remote / "logs/train.log")),
            transport=lambda *_: {"content": base64.b64encode(b"a").decode(), "sha256": "0" * 64},
        )
    assert not store.list("remote_snapshot")
