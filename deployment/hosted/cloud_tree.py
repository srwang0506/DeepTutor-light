"""Capture the personal workspace, including committed SQLite WAL transactions."""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time

from deeptutor.runtime.backup import check_sqlite, relocate_file_references, snapshot_file

SQLITE_HEADER = b"SQLite format 3\x00"
STATE_BLOCK = 128 * 1024
FILE_BLOCK = 1024 * 1024
STATE_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".toml", ".txt", ".md", ".lock"}


def signature(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Workspace contains a link or special file")
    return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode


def inspect_tree(root):
    directories, files, journals = [], {}, {}
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Missing or unsafe workspace data directory")

    def fail(error):
        raise error

    for parent, names, entries in os.walk(root, onerror=fail):
        directory = Path(parent)
        for name in sorted(names):
            path = directory / name
            if path.is_symlink():
                raise ValueError("Workspace contains a directory link")
            directories.append(path.relative_to(root).as_posix())
        for name in entries:
            if name.startswith("._"):
                continue
            path = directory / name
            rel = path.relative_to(root).as_posix()
            suffix = next((s for s in ("-shm", "-wal", "-journal") if name.endswith(s)), None)
            if suffix and (directory / name[: -len(suffix)]).is_file():
                owner = rel[: -len(suffix)]
                with snapshot_file(root, Path(owner)) as stream:
                    if stream.read(16) == SQLITE_HEADER:
                        if suffix != "-shm":
                            journals.setdefault(owner, {})[suffix] = signature(path)
                        continue
            files[rel] = signature(path)
    return sorted(directories), {p: (v, journals.get(p)) for p, v in files.items()}


def snapshot_database(source, dest):
    deadline = time.monotonic() + 15

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("Busy workspace database")

    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=3) as original,
        sqlite3.connect(dest) as copy,
    ):
        original.backup(copy, pages=256, progress=progress, sleep=0.02)
        copy.execute("PRAGMA journal_mode=DELETE")


def describe(stream, kind, pending):
    checksum, pieces, size = sha256(), [], 0
    block_size = STATE_BLOCK if kind == "state" else FILE_BLOCK
    while block := stream.read(block_size):
        checksum.update(block)
        digest = sha256(block).hexdigest()
        piece = pending / (kind + "-" + digest)
        if not piece.exists():
            piece.write_bytes(block)
        pieces.append(digest)
        size += len(block)
    return {"kind": kind, "size": size, "sha256": checksum.hexdigest(), "pieces": pieces}


@dataclass
class Capture:
    manifest: dict
    signatures: dict


def capture(home, previous, signatures, pending):
    root = home / "data"
    # A second directory scan makes the set of files, WALs and descriptors a
    # single local point in time. Remote I/O occurs after this copy is stable.
    for _ in range(5):
        try:
            directories, current = inspect_tree(root)
            files = {}
            for rel, stamp in current.items():
                if signatures.get(rel) == stamp and rel in previous.get("files", {}):
                    files[rel] = previous["files"][rel]
                    continue
                path = root / rel
                with snapshot_file(root, Path(rel)) as source:
                    database = source.read(16) == SQLITE_HEADER
                    source.seek(0)
                    if not database:
                        kind = (
                            "state"
                            if path.suffix.lower() in STATE_SUFFIXES and stamp[0][1] <= STATE_BLOCK
                            else "file"
                        )
                        files[rel] = {
                            **describe(source, kind, pending),
                            "executable": bool(stamp[0][4] & stat.S_IXUSR),
                        }
                        continue
                with tempfile.TemporaryDirectory(dir=pending) as temporary:
                    copy = Path(temporary) / "database"
                    snapshot_database(path, copy)
                    with copy.open("rb") as stream:
                        files[rel] = {**describe(stream, "state", pending), "sqlite": True}
            if (directories, current) == inspect_tree(root):
                return Capture(
                    {"schema": 1, "home": str(home), "dirs": directories, "files": files},
                    current,
                )
        except FileNotFoundError:
            pass
        except ValueError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                raise
        time.sleep(0.03)
    raise RuntimeError("Workspace files kept changing during capture")


def pieces(manifest, kind):
    return {
        digest
        for row in manifest.get("files", {}).values()
        if row["kind"] == kind
        for digest in row["pieces"]
    }


def relative(name):
    path = Path(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != name:
        raise ValueError("Invalid path in the workspace manifest")
    return path


def restore_tree(home, manifest, fetch):
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported workspace manifest")
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home, prefix=".cloud-restore-") as temporary:
        staging = Path(temporary)
        root = staging / "data"
        root.mkdir(mode=0o700)
        for name in manifest["dirs"]:
            (root / relative(name)).mkdir(parents=True, exist_ok=True)
        for name, row in manifest["files"].items():
            target = root / relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            checksum, size = sha256(), 0
            with target.open("xb") as output:
                for digest in row["pieces"]:
                    block = fetch(row["kind"], digest)
                    checksum.update(block)
                    size += len(block)
                    output.write(block)
            if size != row["size"] or checksum.hexdigest() != row["sha256"]:
                raise ValueError("Workspace file failed verification")
            if row.get("sqlite"):
                check_sqlite(target)
            if row.get("executable"):
                target.chmod(0o700)
        if str(home) != manifest["home"]:
            relocate_file_references(staging, manifest["home"], home)
        target = home / "data"
        old = staging / "previous"
        if target.exists():
            target.rename(old)
        try:
            root.rename(target)
        except BaseException:
            if old.exists():
                old.rename(target)
            raise
