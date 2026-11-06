"""Project-owned, read-only SSH and GitHub connectors with immutable snapshots."""

import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Literal
from urllib.parse import quote

from pydantic import Field, model_validator

from . import remote_reader
from .database import Conflict, encoded, sha
from .models import Command, RunInput


class ConnectionInput(Command):
    title: str = Field(min_length=1, max_length=120)
    provider: Literal["ssh", "github"]
    alias: str = Field(default="", max_length=200)
    roots: list[str] = Field(default_factory=list, max_length=8)
    repository: str = Field(default="", max_length=300)
    ref: str = Field(default="HEAD", min_length=1, max_length=200)
    enabled: bool = True

    @model_validator(mode="after")
    def valid_target(self):
        if self.provider == "ssh":
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.alias):
                raise ValueError("Use a concrete SSH alias from your local configuration.")
            if not self.roots or any(
                not p.startswith("/")
                or len(p) > 1000
                or "\x00" in p
                or ".." in PurePosixPath(p).parts
                or not remote_reader.permitted(p)
                for p in self.roots
            ):
                raise ValueError("Select 1–8 absolute, non-protected remote directories.")
        elif not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", self.repository
        ):
            raise ValueError("Use a GitHub owner/repository name.")
        return self


class ReadRequest(Command):
    operation: Literal["probe", "list", "read", "repository"]
    path: str = Field(default="", max_length=1500)
    commit: str = Field(default="", max_length=64)


class ProcessCancelled(Exception):
    pass


def bounded_process(
    argv, payload=b"", timeout=30, limit=2_000_000, env=None, cwd=None, cancelled=None
):
    """Bound both pipes while running; timeout/overflow terminate the process group."""
    if len(payload) > 16384:
        raise ValueError("Connection request exceeds the supported size.")
    with tempfile.TemporaryFile() as incoming:
        incoming.write(payload)
        incoming.seek(0)
        process = subprocess.Popen(
            argv,
            stdin=incoming,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
            cwd=cwd,
        )
        output, error = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, output)
                selector.register(process.stderr, selectors.EVENT_READ, error)
                while selector.get_map():
                    if cancelled and cancelled():
                        raise ProcessCancelled("Task cancelled; its process group was stopped.")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "Connection timed out. The remote task was stopped; retry when available."
                        )
                    for key, _ in selector.select(min(remaining, 0.25)):
                        part = os.read(key.fileobj.fileno(), 65536)
                        if not part:
                            selector.unregister(key.fileobj)
                            continue
                        key.data.extend(part)
                        if len(output) + len(error) > limit:
                            raise ValueError(
                                "Remote response exceeds the size limit; select a smaller file or directory."
                            )
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
            return process.returncode, bytes(output), bytes(error)
        finally:
            # A descendant may still hold pipes after the direct child has exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()


def create_connection(store, command, identifier=None, expected=None):
    with store.db(write=True) as db:
        return store.put(
            db, "connection", command.model_dump(), identifier=identifier, expected=expected
        )


def ssh_read(config, request):
    executable = shutil.which("ssh")
    if not executable:
        raise ValueError("OpenSSH is unavailable on this computer.")
    helper = Path(remote_reader.__file__).read_text()
    payload = encoded({**request.model_dump(), "roots": config["roots"]}).encode()
    code, raw, error = bounded_process(
        [
            executable,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ForwardX11=no",
            "-o",
            "PermitLocalCommand=no",
            "--",
            config["alias"],
            "python3 -I -c " + shlex.quote(helper),
        ],
        payload,
    )
    if code:
        message = error.decode(errors="replace")
        if "Host key verification failed" in message or "REMOTE HOST IDENTIFICATION" in message:
            raise ValueError(
                "SSH host key is unknown or changed. Verify this host in your local SSH client before reconnecting."
            )
        if "Permission denied" in message:
            raise ValueError(
                "SSH authentication failed. Unlock your key in the local SSH agent, then retry."
            )
        raise ValueError(
            "SSH connection failed. Check the alias, network, local authentication and remote Python 3.9+."
        )
    try:
        response = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(
            "The host did not return the read protocol. Check Python and shell startup output."
        ) from exc
    if not response.get("ok"):
        raise ValueError(response.get("error", "Remote read failed."))
    return response["result"]


def github_api(endpoint):
    executable = shutil.which("gh")
    if not executable:
        raise ValueError(
            "Install GitHub CLI and sign in locally with gh auth login to read repositories."
        )
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_HOST": "github.com", "GH_PAGER": "cat"}
    code, raw, _ = bounded_process(
        [executable, "api", "--hostname", "github.com", "--method", "GET", endpoint], env=env
    )
    if code:
        raise ValueError(
            "GitHub read failed. Check repository access, the selected revision and local gh authentication; rate limits may also apply."
        )
    return json.loads(raw)


def github_read(config, request):
    repository_name = config["repository"]
    prefix = "repos/" + repository_name
    if request.commit and not re.fullmatch(r"[a-f0-9]{40}", request.commit):
        raise ValueError("Use the full commit SHA returned by a repository inspection.")
    commit = (
        request.commit or github_api(prefix + "/commits/" + quote(config["ref"], safe=""))["sha"]
    )
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("GitHub did not return a valid immutable commit.")
    if request.operation == "probe":
        return {"repository": repository_name, "commit": commit, "mode": "read_only", "protocol": 1}
    path = request.path.strip("/")
    if ".." in PurePosixPath(path).parts or not remote_reader.permitted(path):
        raise ValueError("Choose a non-protected repository path.")
    if request.operation == "repository":
        tree = github_api(prefix + "/git/trees/" + commit + "?recursive=1")
        entries = [r for r in tree.get("tree", []) if remote_reader.permitted(r["path"])]
        return {
            "repository": repository_name,
            "commit": commit,
            "entries": [
                {
                    "name": r["path"],
                    "path": r["path"],
                    "kind": "directory" if r["type"] == "tree" else "file",
                    "size": r.get("size", 0),
                }
                for r in entries[:300]
                if r.get("mode") in {"040000", "100644", "100755"}
            ],
            "truncated": bool(tree.get("truncated")) or len(entries) > 300,
            "analysis": "static_only",
        }
    # Inspect tree modes before reading blobs: the Contents API may dereference
    # repository symlinks. A symlink to a protected file must not bypass the scope.
    parts = PurePosixPath(path).parts
    if len(parts) > 20:
        raise ValueError("Repository path exceeds the supported depth.")
    tree_sha = github_api(prefix + "/git/commits/" + commit)["tree"]["sha"]
    entry = None
    for index in range(len(parts) + 1):
        tree = github_api(prefix + "/git/trees/" + tree_sha)
        if tree.get("truncated"):
            raise ValueError("GitHub truncated this directory. Choose a smaller repository scope.")
        if index == len(parts):
            if request.operation != "list":
                raise ValueError("Select a regular file to read.")
            entries = [
                {
                    "name": r["path"],
                    "path": str(PurePosixPath(path) / r["path"]),
                    "size": r.get("size", 0),
                    "kind": "directory" if r["type"] == "tree" else "file",
                }
                for r in tree.get("tree", [])
                if r.get("mode") in {"040000", "100644", "100755"}
                and remote_reader.permitted(r["path"])
            ]
            return {
                "path": path,
                "commit": commit,
                "entries": entries[:300],
                "truncated": len(entries) > 300,
            }
        entry = next((r for r in tree.get("tree", []) if r["path"] == parts[index]), None)
        if not entry or entry.get("mode") not in {"040000", "100644", "100755"}:
            raise ValueError("Path is missing or is a symbolic link or submodule.")
        if entry["type"] == "tree":
            tree_sha = entry["sha"]
            continue
        if index != len(parts) - 1 or request.operation != "read":
            raise ValueError("Select a directory to browse.")
        if entry.get("size", 0) > remote_reader.MAX_FILE:
            raise ValueError("Select a file up to 512 KiB.")
        break
    data = github_api(prefix + "/git/blobs/" + entry["sha"])
    if data.get("encoding") != "base64":
        raise ValueError("GitHub did not return file content.")
    content = base64.b64decode(data["content"])
    if len(content) > remote_reader.MAX_FILE:
        raise ValueError("Select a file up to 512 KiB.")
    return {
        "path": path,
        "commit": commit,
        "repository": repository_name,
        "sha256": sha(content),
        "size": len(content),
        "content": base64.b64encode(content).decode(),
        "encoding": "base64",
    }


def read_connection(store, identifier, request, *, transport=None):
    connection = store.get(identifier, "connection")
    config = connection["data"]
    if not config["enabled"]:
        raise ValueError("This connection is disconnected. Enable it before reading new data.")
    started = time.time()
    try:
        result = (transport or (ssh_read if config["provider"] == "ssh" else github_read))(
            config, request
        )
        content_hash = None
        if request.operation == "read":
            content = base64.b64decode(result.pop("content"), validate=True)
            if len(content) > remote_reader.MAX_FILE or sha(content) != result["sha256"]:
                raise ValueError("Remote content failed its integrity check.")
            content_hash = store.save_blob(content)
            result["content_hash"] = content_hash
        with store.db(write=True) as db:
            current = store.record(db, identifier, "connection")
            if current["revision"] != connection["revision"] or not current["data"]["enabled"]:
                raise Conflict(
                    "Connection scope changed during reading. Review it before retrying."
                )
            row = store.put(
                db,
                "remote_snapshot",
                {
                    "connection_id": identifier,
                    "connection_revision": connection["revision"],
                    "connection_title": config["title"],
                    "provider": config["provider"],
                    "target": config["alias"]
                    if config["provider"] == "ssh"
                    else config["repository"],
                    "request": request.model_dump(),
                    "started": started,
                    "finished": time.time(),
                    "status": "succeeded",
                    "result": result,
                    "content_hash": content_hash,
                },
            )
            # A frozen snapshot remains valid when connection preferences change.
            return row
    except (ValueError, OSError, TimeoutError) as exc:
        with store.db(write=True) as db:
            store.put(
                db,
                "connection_event",
                {
                    "connection_id": identifier,
                    "operation": request.operation,
                    "path": request.path,
                    "started": started,
                    "finished": time.time(),
                    "status": "failed",
                    "error": str(exc)[:1000],
                },
            )
        raise ValueError(str(exc)) from exc


def snapshot_text(store, identifier):
    row = store.get(identifier, "remote_snapshot")
    digest = row["data"].get("content_hash")
    if not digest:
        raise ValueError("This snapshot does not contain a file.")
    try:
        text = store.blob_path(digest).read_bytes().decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(
            "The selected file is binary. Choose a UTF-8 log, configuration or manifest."
        ) from exc
    if "\x00" in text:
        raise ValueError("The selected file is binary.")
    return {"text": text, "hash": digest, "snapshot": row}


def import_snapshot_run(store, snapshot_id, command: RunInput, repository_snapshot_id=""):
    from .experiments import create_run

    snapshot = store.get(snapshot_id, "remote_snapshot")
    source = snapshot_text(store, snapshot_id)
    provenance = snapshot["data"]["result"]
    repository_snapshot = None
    if repository_snapshot_id:
        repository_snapshot = store.get(repository_snapshot_id, "remote_snapshot")
        d = repository_snapshot["data"]
        if (
            d["request"]["operation"] != "repository"
            or d["connection_id"] != snapshot["data"]["connection_id"]
        ):
            raise ValueError("Select a repository inspection from the same connection.")
        repo_path = d["result"].get("path", "")
        if repo_path and not PurePosixPath(provenance["path"]).is_relative_to(repo_path):
            raise ValueError("This file is outside the selected repository.")
        provenance = {**provenance, **d["result"]}
    # A user can record a declared outcome, but connector metadata never verifies execution.
    command = command.model_copy(update={"log": source["text"]})
    from .tracking import run_draft

    extracted = run_draft(source["text"], command.title)
    data = {
        "tracking_snapshot": {
            key: extracted[key]
            for key in ("adapter", "external_id", "declared_status", "metric_samples", "warnings")
        },
        "remote_source": {
            "snapshot_id": snapshot_id,
            "repository_snapshot_id": repository_snapshot_id or None,
            "content_hash": source["hash"],
            "path": snapshot["data"]["result"]["path"],
            "target": snapshot["data"]["target"],
            "retrieved_at": snapshot["data"]["finished"],
            "repository_observed_commit": provenance.get("commit"),
            "repository_dirty": provenance.get("dirty"),
        },
        "execution_verified": False,
    }
    sources = [(snapshot_id, "imported_log")]
    if repository_snapshot:
        sources.append((repository_snapshot_id, "repository_snapshot"))
    return create_run(store, command, provenance=data, sources=sources)


def snapshot_run_draft(store, identifier):
    from .tracking import run_draft

    source = snapshot_text(store, identifier)
    name = PurePosixPath(source["snapshot"]["data"]["result"]["path"]).name
    result = run_draft(source["text"], name)
    result["snapshot_id"] = identifier
    result["source_hash"] = source["hash"]
    return result
