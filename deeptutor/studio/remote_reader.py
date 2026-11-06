"""Self-contained read-only protocol, executed by the selected host's Python.

No package installation, uploaded helper, shell commands or user code execution.
The caller applies another output/time limit around the SSH process.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

MAX_FILE = 512 * 1024
MAX_ITEMS = 300
DENIED = {".ssh", ".aws", ".gnupg", ".git", "node_modules", ".venv", "__pycache__", ".codex"}


def permitted(path):
    for part in Path(path).parts:
        low = part.lower()
        if low in DENIED or low == ".env" or low.startswith(".env."):
            return False
        if low.endswith((".pem", ".key", ".p12", ".pfx")) or low in {
            "id_rsa",
            "id_ed25519",
            "credentials",
            "auth.json",
            "known_hosts",
        }:
            return False
    return True


def checked(path, roots):
    raw = Path(path)
    if not raw.is_absolute() or not permitted(raw):
        raise ValueError("Path is outside the readable scope or is a protected file.")
    resolved = raw.resolve(strict=True)
    if not permitted(resolved) or not any(resolved.is_relative_to(r) for r in roots):
        raise ValueError("Path is outside the selected directories.")
    return resolved


def read_file(path):
    # O_NOFOLLOW rejects a link swapped into place after validation.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
            raise ValueError("Choose a regular file no larger than 512 KiB.")
        data = source.read(MAX_FILE + 1)
        after = os.fstat(source.fileno())
    if len(data) > MAX_FILE or (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("The file changed during reading; retry to capture a consistent snapshot.")
    return {
        "path": str(path),
        "size": len(data),
        "mtime_ns": after.st_mtime_ns,
        "sha256": hashlib.sha256(data).hexdigest(),
        "content": base64.b64encode(data).decode(),
        "encoding": "base64",
    }


def git(path, *args):
    # Disable hooks, external diff/text converters, optional index writes and global config.
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for key in list(env):
        if key.startswith("GIT_") and key not in {
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_OPTIONAL_LOCKS",
            "GIT_TERMINAL_PROMPT",
        }:
            del env[key]
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.worktree=" + str(path),
                "-c",
                "status.submoduleSummary=false",
                "-c",
                "diff.ignoreSubmodules=all",
                "-c",
                "core.hooksPath=" + os.devnull,
                "-C",
                str(path),
                *args,
            ],
            stdout=output,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=8,
            env=env,
            check=False,
        )
        if result.returncode:
            raise ValueError("The selected directory is not a readable Git repository.")
        output.seek(0)
        value = output.read(MAX_FILE + 1)
        if len(value) > MAX_FILE:
            raise ValueError("Repository response is too large; select a narrower directory.")
        return value.decode("utf-8", errors="replace").strip()


def repository(path, roots):
    # Require an ordinary .git directory within the approved roots. Worktrees with
    # metadata elsewhere must be selected separately, rather than silently following it.
    marker = path / ".git"
    if not marker.is_dir() or marker.is_symlink():
        raise ValueError("Select a repository root with a local .git directory.")
    commit = git(path, "rev-parse", "--verify", "HEAD")
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    # Even `git status` may run repository-defined clean filters while refreshing
    # an index. Object/ref reads cannot establish worktree cleanliness safely.
    # Keep that distinction explicit instead of executing untrusted converters.
    candidates = []
    # A bounded walk does not enter dependency directories or follow symlinks.
    visited = 0
    for current, dirs, files in os.walk(path, followlinks=False):
        depth = len(Path(current).relative_to(path).parts)
        dirs[:] = (
            sorted(d for d in dirs if permitted(d) and not (Path(current) / d).is_symlink())[:80]
            if depth < 3
            else []
        )
        for name in sorted(files):
            visited += 1
            if visited > 3000:
                break
            candidate = Path(current) / name
            if candidate.is_symlink() or not permitted(name):
                continue
            low = name.lower()
            category = (
                "manifest"
                if low in {"manifest.json", "run.json", "run_manifest.json"}
                else "environment"
                if low in {"pyproject.toml", "requirements.txt", "environment.yml", "package.json"}
                else "configuration"
                if low.endswith((".yaml", ".yml", ".toml"))
                else "metrics"
                if low.endswith((".csv", ".jsonl"))
                else "log"
                if low.endswith(".log")
                else "documentation"
                if low.startswith("readme")
                else None
            )
            if category:
                candidates.append(
                    {
                        "path": str(candidate),
                        "relative": str(candidate.relative_to(path)),
                        "category": category,
                        "size": candidate.stat().st_size,
                    }
                )
            if len(candidates) >= 150:
                break
        if visited > 3000 or len(candidates) >= 150:
            break
    return {
        "path": str(path),
        "commit": commit,
        "branch": branch,
        "tracked_changes": None,
        "dirty": None,
        "dirty_state": "unknown",
        "dirty_reason": "Read-only inspection does not execute worktree filters.",
        "candidates": candidates,
        "scan_complete": visited <= 3000 and len(candidates) < 150,
        "analysis": "static_only",
        "patch_captured": False,
    }


def dispatch(request):
    roots = []
    for item in request.get("roots", []):
        raw = Path(item)
        if not raw.is_absolute() or not permitted(raw):
            raise ValueError("Select absolute, non-protected directories.")
        resolved = raw.resolve(strict=True)
        if not resolved.is_dir() or not permitted(resolved):
            raise ValueError("Selected root is not a readable directory.")
        roots.append(resolved)
    if not 1 <= len(roots) <= 8:
        raise ValueError("Select between one and eight readable directories.")
    operation = request.get("operation")
    if operation == "probe":
        return {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "roots": [str(r) for r in roots],
            "protocol": 1,
            "mode": "read_only",
        }
    path = checked(request.get("path", ""), roots)
    if operation == "read":
        return read_file(path)
    if operation == "repository":
        return repository(path, roots)
    if operation == "list":
        if not path.is_dir():
            raise ValueError("Select a directory to browse.")
        entries = []
        truncated = False
        # scandir is incremental: a huge directory never becomes a huge in-memory list.
        with os.scandir(path) as iterator:
            inspected = 0
            for item in iterator:
                inspected += 1
                if inspected > 3000 or len(entries) >= MAX_ITEMS:
                    truncated = True
                    break
                if not permitted(item.name) or item.is_symlink():
                    continue
                info = item.stat(follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "path": item.path,
                        "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                    }
                )
        return {
            "path": str(path),
            "entries": sorted(entries, key=lambda x: (x["kind"] != "directory", x["name"])),
            "truncated": truncated,
        }
    raise ValueError("Unsupported read operation.")


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("Request exceeds the supported size.")
        result = {"ok": True, "result": dispatch(json.loads(raw))}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:1000], "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
