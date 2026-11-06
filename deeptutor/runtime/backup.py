"""Offline, verified backups of the complete runtime data tree.

Stop API, CLI workers and external writers first. The study API process holds a
lock; backup takes it exclusively, preventing a half-updated cross-store copy.
SQLite's backup API includes committed WAL contents. Restore never overwrites.
"""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time

from filelock import FileLock, Timeout


@contextmanager
def runtime_lock(home):
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    # This deployment intentionally runs one API instance. filelock provides
    # matching ownership checks on macOS, Linux and Windows.
    lock = FileLock(home / ".study-runtime.lock", timeout=0)
    try:
        with lock:
            yield
    except Timeout as exc:
        raise RuntimeError(
            "Runtime is in use. Stop DeepTutor and its workers before backup or restore."
        ) from exc


def digest(path):
    result = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def is_sqlite(path):
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def check_sqlite(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError(f"Database integrity check failed: {path.name}")


def backup(home, destination):
    home, destination = Path(home).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(home):
        raise ValueError("Choose a new backup directory outside the runtime home")
    source = home / "data"
    if not source.is_dir():
        raise ValueError("Runtime data directory is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        runtime_lock(home),
        tempfile.TemporaryDirectory(dir=destination.parent, prefix=".study-backup-") as staging,
    ):
        stage = Path(staging)
        (stage / "data").mkdir()
        manifest = {
            "schema": 1,
            "created_at": time.time(),
            "source_home": str(home),
            "files": [],
            "directories": [],
        }
        for src in sorted(source.rglob("*")):
            if src.is_symlink():
                raise ValueError(
                    f"External symbolic link must be materialized before backup: {src.relative_to(home)}"
                )
            if src.is_dir():
                manifest["directories"].append(str(src.relative_to(home)))
                (stage / src.relative_to(home)).mkdir(parents=True, exist_ok=True)
                continue
            if (
                not src.is_file()
                or src.name.startswith("._")
                or src.name.endswith(("-wal", "-shm"))
            ):
                continue
            relative = src.relative_to(home)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            database = is_sqlite(src)
            if database:
                with (
                    sqlite3.connect(src.as_uri() + "?mode=ro", uri=True) as original,
                    sqlite3.connect(target) as snapshot,
                ):
                    original.backup(snapshot)
                    snapshot.execute("PRAGMA journal_mode=DELETE")
                check_sqlite(target)
            else:
                shutil.copyfile(src, target)
            manifest["files"].append(
                {
                    "path": str(relative),
                    "sha256": digest(target),
                    "size": target.stat().st_size,
                    "sqlite": database,
                }
            )
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        os.chmod(stage, 0o700)
        stage.rename(destination)
    return manifest


def verify(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / "manifest.json").read_text())
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("Unsupported backup manifest")
    # rglob does not descend into directory links, whereas copytree used to do
    # so. Reject every link, including unlisted/empty directory links, first.
    if (snapshot / "data").is_symlink():
        raise ValueError("Backup data cannot be a symbolic link")
    for path in (snapshot / "data").rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Backup contains a symbolic link or special file")
    for directory in manifest.get("directories", []):
        rel = Path(directory)
        if rel.is_absolute() or ".." in rel.parts or rel.parts[:1] != ("data",):
            raise ValueError("Invalid backup directory")
        if not (snapshot / rel).is_dir() or (snapshot / rel).is_symlink():
            raise ValueError("Missing or unsafe backup directory")
    paths = set()
    for row in manifest["files"]:
        relative = Path(row["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parts[:1] != ("data",)
            or str(relative) in paths
        ):
            raise ValueError("Invalid backup path")
        target = snapshot / relative
        if (
            target.is_symlink()
            or not target.resolve().is_relative_to(snapshot)
            or not target.is_file()
        ):
            raise ValueError("Missing or unsafe backup file")
        if target.stat().st_size != row["size"] or digest(target) != row["sha256"]:
            raise ValueError(f"Backup checksum mismatch: {relative}")
        if row["sqlite"]:
            check_sqlite(target)
        paths.add(str(relative))
    actual = {
        str(p.relative_to(snapshot))
        for p in (snapshot / "data").rglob("*")
        if p.is_file() and not p.name.startswith("._")
    }
    if actual != paths:
        raise ValueError("Backup file list does not match manifest")
    return manifest


def relocate_file_references(stage, source_home, destination):
    """Rebase structured local file references; keep prose and identifiers intact."""
    if not source_home:
        return
    old = str(Path(source_home) / "data") + os.sep
    new = str(destination / "data") + os.sep
    path_keys = {
        "storage_path",
        "version_path",
        "candidate_storage",
        "file_path",
        "path",
        "source_file",
    }

    def rebase(value, key=""):
        if isinstance(value, dict):
            return {k: rebase(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [rebase(v, key) for v in value]
        if isinstance(value, str) and key == "_node_content":
            # LlamaIndex stores a second JSON document inside each BM25 row.
            try:
                return json.dumps(rebase(json.loads(value)), ensure_ascii=False)
            except ValueError:
                return value
        if isinstance(value, str) and key in path_keys and value.startswith(old):
            return new + value[len(old) :]
        return value

    # Project identities and selected-source edges survive a restore. Rebase only
    # managed local roots inside the snapshot, never user-authored instructions.
    for catalog in (stage / "data").rglob("workspaces.sqlite3"):
        if catalog.is_symlink():
            continue
        with sqlite3.connect(catalog) as conn:
            for workspace_id, payload in conn.execute(
                "SELECT id,payload FROM workspaces"
            ).fetchall():
                row = json.loads(payload)
                updated = rebase(row)
                if updated != row:
                    conn.execute(
                        "UPDATE workspaces SET payload=? WHERE id=?",
                        (json.dumps(updated, ensure_ascii=False), workspace_id),
                    )
            root = conn.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
            if root:
                value = json.loads(root[0])
                rebased = rebase(value, "path")
                if rebased != value:
                    conn.execute(
                        "UPDATE metadata SET value=? WHERE key='root'", (json.dumps(rebased),)
                    )

    for path in (stage / "data").rglob("*"):
        if (
            path.name.startswith("._")
            or path.suffix not in {".json", ".jsonl"}
            or not path.is_file()
        ):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            if old not in raw:
                continue
            if path.suffix == ".jsonl":
                lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
                updated = [rebase(line) for line in lines]
                if lines != updated:
                    path.write_text(
                        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in updated)
                    )
            else:
                original = json.loads(raw)
                updated = rebase(original)
                if updated != original:
                    path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
        except (ValueError, UnicodeError):
            continue


def restore(snapshot, destination):
    snapshot, destination = Path(snapshot).resolve(), Path(destination).resolve()
    manifest = verify(snapshot)
    if destination.exists():
        raise ValueError(
            "Restore requires a new, empty runtime home; existing data is never overwritten"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".study-restore-") as staging:
        stage = Path(staging)
        (stage / "data").mkdir()
        for directory in manifest.get("directories", []):
            (stage / directory).mkdir(parents=True, exist_ok=True)
        # Restore exactly the validated manifest. Never recursively copy data
        # that appeared after validation, or traverse a swapped directory link.
        for row in manifest["files"]:
            relative = Path(row["path"])
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with snapshot_file(snapshot, relative) as source, target.open("xb") as output:
                checksum = sha256()
                size = 0
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > row["size"]:
                        raise ValueError("Backup changed during restore")
                    checksum.update(chunk)
                    output.write(chunk)
                if size != row["size"] or checksum.hexdigest() != row["sha256"]:
                    raise ValueError("Backup changed during restore")
            if row["sqlite"]:
                check_sqlite(target)
        relocate_file_references(stage, manifest.get("source_home"), destination)
        os.chmod(stage, 0o700)
        stage.rename(destination)
    return manifest


@contextmanager
def snapshot_file(root, relative):
    """Open each path component without following links, including swap races."""
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("Safe restore requires no-follow directory handles on this platform")
    handles = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handles.append(directory)
        for part in relative.parts[:-1]:
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            handles.append(directory)
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("Backup entry is not a regular file")
            yield source
    except OSError as exc:
        raise ValueError("Backup path changed or contains a link") from exc
    finally:
        for handle in reversed(handles):
            os.close(handle)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "verify", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    args = parser.parse_args()
    if args.operation != "verify" and not args.destination:
        parser.error("destination is required")
    result = (
        verify(args.source)
        if args.operation == "verify"
        else globals()[args.operation](args.source, args.destination)
    )
    print(f"{args.operation}: verified {len(result['files'])} files")


if __name__ == "__main__":
    main()
