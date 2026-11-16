"""Port selection must reach both child processes and the readiness probe."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def launcher():
    path = Path(__file__).resolve().parents[2] / "scripts/study.py"
    spec = importlib.util.spec_from_file_location("study_port_test_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_ports_remain_compatible(launcher):
    args = launcher.parse_args(["run"])
    assert args.port == 3782
    assert args.backend_port == 8001


@pytest.mark.parametrize("flag", ["--port", "--backend-port"])
@pytest.mark.parametrize("value", ["0", "-1", "65536", "not-a-port", "1.5"])
def test_invalid_ports_are_rejected_before_runtime_changes(launcher, flag, value):
    before = dict(os.environ)
    with pytest.raises(SystemExit) as rejected:
        launcher.main(["run", flag, value])
    assert rejected.value.code == 2
    assert dict(os.environ) == before


@pytest.mark.parametrize("command", ["run", "doctor"])
def test_frontend_and_backend_cannot_share_a_port(launcher, command):
    with pytest.raises(SystemExit) as rejected:
        launcher.parse_args([command, "--backend-port", "3782"])
    assert rejected.value.code == 2


@pytest.mark.parametrize("port", ["1", "65535"])
def test_valid_port_boundaries_are_accepted(launcher, port):
    assert launcher.parse_args(["run", "--backend-port", port]).backend_port == int(port)


def dependencies_available(launcher, monkeypatch):
    monkeypatch.setattr(
        launcher, "importlib", SimpleNamespace(util=SimpleNamespace(find_spec=lambda _: True))
    )
    monkeypatch.setattr(launcher, "shutil", SimpleNamespace(which=lambda name: f"/fake/bin/{name}"))


def test_custom_backend_reaches_argv_environment_and_readiness(
    launcher, monkeypatch, tmp_path, capsys
):
    dependencies_available(launcher, monkeypatch)
    launches = []

    def popen(argv, **kwargs):
        # Pure fakes: neither a process nor a socket is created or terminated.
        child = Mock()
        child.poll.return_value = None if not launches else 0
        launches.append((argv, kwargs.get("env", dict(os.environ)), child))
        return child

    monkeypatch.setattr(
        launcher,
        "subprocess",
        SimpleNamespace(Popen=popen, TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(launcher.signal, "signal", Mock())
    response = Mock()
    response.__enter__ = Mock(return_value=SimpleNamespace(status=200))
    response.__exit__ = Mock(return_value=False)
    probe = Mock(return_value=response)
    monkeypatch.setattr(launcher, "urlopen", probe)
    launcher.main(
        ["run", "--dev", "--home", str(tmp_path), "--backend-port", "8015", "--port", "3782"]
    )

    assert len(launches) == 2
    backend_argv, backend_env, _ = launches[0]
    assert backend_argv[backend_argv.index("--port") + 1] == "8015"
    frontend_argv, frontend_env, _ = launches[1]
    assert frontend_argv[frontend_argv.index("--port") + 1] == "3782"
    for env in (backend_env, frontend_env):
        assert env["BACKEND_PORT"] == "8015"
        assert env["NEXT_PUBLIC_API_BASE"] == "http://127.0.0.1:8015"
        assert env["DEEPTUTOR_API_BASE_URL"] == "http://127.0.0.1:8015"
        assert env["DEEPTUTOR_LOCAL_ORIGINS"] == "http://127.0.0.1:3782,http://localhost:3782"
    assert frontend_env["PORT"] == "3782"
    probe.assert_called_once_with("http://127.0.0.1:8015/health/ready", timeout=1)
    assert "Backend API: http://127.0.0.1:8015" in capsys.readouterr().out
    assert "http://127.0.0.1:3782" in (tmp_path / "local-launch-url").read_text()


def test_doctor_reports_custom_backend_without_starting_children(
    launcher, monkeypatch, tmp_path, capsys
):
    dependencies_available(launcher, monkeypatch)
    popen = Mock(side_effect=AssertionError("doctor must not launch a child"))
    monkeypatch.setattr(launcher, "subprocess", SimpleNamespace(Popen=popen))
    monkeypatch.setitem(
        sys.modules,
        "deeptutor.services.config.runtime_settings",
        SimpleNamespace(load_document_parsing_settings=lambda: {"engine": "test"}),
    )
    monkeypatch.setitem(
        sys.modules,
        "deeptutor.services.embedding.config",
        SimpleNamespace(get_embedding_config=lambda: SimpleNamespace(model="test")),
    )
    monkeypatch.setitem(
        sys.modules,
        "deeptutor.services.rag.pipelines.llamaindex.retrievers",
        SimpleNamespace(BM25_OPTIONS={}),
    )
    launcher.main(["doctor", "--home", str(tmp_path), "--backend-port", "8015"])
    assert "Backend API: http://127.0.0.1:8015" in capsys.readouterr().out
    assert os.environ["DEEPTUTOR_API_BASE_URL"] == "http://127.0.0.1:8015"
    popen.assert_not_called()
