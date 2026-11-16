"""Authorized, complete skill snapshots for a task's read-only sandbox mount.

This is not an import gate: only the skill selected by runtime resolution may be
copied. Binary support assets are retained; existing user import limits stay in
place. A changed source or damaged published snapshot fails closed rather than
replacing files underneath an active execution.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from deeptutor.services.skill.runtime import resolve_runtime_skill
from deeptutor.services.skill.service import InvalidSkillNameError, SkillNotFoundError

_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_MANIFEST = ".deeptutor-skill-manifest.json"
_MAX_FILES = 4096
_MAX_BYTES = 64 * 1024 * 1024
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


class SkillExecutionError(ValueError):
    """The selected package cannot be safely staged or has changed in flight."""


def _directory(path: Path, *, create: bool = False) -> Path:
    """Do not silently resolve a caller's symlink, including a parent link."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if create:
            try:
                current.mkdir()
            except FileExistsError:
                pass
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise SkillExecutionError("Skill directory does not exist") from exc
        if not stat.S_ISDIR(mode):
            raise SkillExecutionError("Skill paths must contain only real directories")
    return absolute


def _source(name: str) -> Path:
    service = resolve_runtime_skill(name)
    resolved = service._resolve_skill_dir(name)
    if resolved is None:
        raise SkillNotFoundError(name)
    path, layer = resolved
    root = service._builtin_root if layer == "builtin" else service.root
    assert root is not None
    root = _directory(root)
    path = _directory(path)
    if path.parent != root or path.name != name:
        raise SkillExecutionError("Resolved skill is outside its authorized directory")
    return path


def _open_directory(path: Path) -> int:
    fd = os.open(path.anchor, _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _scan(
    root: Path, *, copy_to: Path | None = None, published: bool = False, staging: bool = False
) -> dict:
    """Read via no-follow directory/file descriptors and hash the exact bytes."""
    files: dict[str, dict] = {}
    directories: list[str] = []
    total = 0

    def visit(fd: int, relative: Path) -> None:
        nonlocal total
        for item in sorted(os.scandir(fd), key=lambda entry: entry.name):
            rel = relative / item.name
            key = rel.as_posix()
            if key == _MANIFEST:
                if published:
                    continue
                raise SkillExecutionError("Package uses a reserved snapshot filename")
            mode = item.stat(follow_symlinks=False).st_mode
            if published and mode & 0o222:
                raise SkillExecutionError("Published skill snapshot is writable")
            if stat.S_ISDIR(mode):
                child = os.open(item.name, _DIR_FLAGS, dir_fd=fd)
                try:
                    directories.append(key)
                    if copy_to is not None:
                        (copy_to / rel).mkdir()
                    visit(child, rel)
                finally:
                    os.close(child)
            elif stat.S_ISREG(mode):
                if len(files) >= _MAX_FILES:
                    raise SkillExecutionError("Skill execution package has too many files")
                source_fd = os.open(
                    item.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                )
                with os.fdopen(source_fd, "rb") as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise SkillExecutionError("Skill package contains a special file")
                    digest = hashlib.sha256()
                    size = 0
                    target = (copy_to / rel).open("xb") if copy_to is not None else None
                    try:
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            total += len(chunk)
                            if total > _MAX_BYTES:
                                raise SkillExecutionError(
                                    "Skill execution package exceeds size limit"
                                )
                            digest.update(chunk)
                            if target is not None:
                                target.write(chunk)
                    finally:
                        if target is not None:
                            target.close()
                    after = os.fstat(source.fileno())
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise SkillExecutionError("Skill package changed while being read")
                executable = bool(before.st_mode & 0o111)
                files[key] = {"sha256": digest.hexdigest(), "size": size, "executable": executable}
                if copy_to is not None:
                    (copy_to / rel).chmod(0o555 if executable else 0o444)
            else:
                raise SkillExecutionError("Skill package contains a symbolic link or special file")

    fd = _open_directory(root)
    try:
        if published and not staging and os.fstat(fd).st_mode & 0o222:
            raise SkillExecutionError("Published skill snapshot is writable")
        visit(fd, Path())
    finally:
        os.close(fd)
    if "SKILL.md" not in files:
        raise SkillExecutionError("Skill package is missing SKILL.md")
    return {"files": files, "directories": sorted(directories)}


def _verify(destination: Path, expected: dict, *, staging: bool = False) -> None:
    _directory(destination)
    directory_fd = _open_directory(destination)
    try:
        fd = os.open(_MANIFEST, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    with os.fdopen(fd, "rb") as stream:
        mode = os.fstat(stream.fileno()).st_mode
        if not stat.S_ISREG(mode) or mode & 0o222:
            raise SkillExecutionError("Skill snapshot manifest is writable")
        try:
            manifest = json.load(stream)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SkillExecutionError("Invalid skill snapshot manifest") from exc
    if (
        manifest != expected
        or _scan(destination, published=True, staging=staging) != expected["content"]
    ):
        raise SkillExecutionError("Skill snapshot differs from its authorized package")


def stage_skill(task_dir: Path, name: str) -> Path:
    """Stage the authorized complete package; return its task-local directory.

    Call on every admission, including repeat reads, so workspace restrictions or
    revoked assignments cannot be bypassed by a previously published snapshot.
    The caller mounts the returned directory read-only and injects its *logical*
    sandbox path into read_skill; source account paths never leave this service.
    """
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise SkillExecutionError("This execution host does not support secure skill snapshots")
    slug = name.strip().lower()
    if not _NAME.fullmatch(slug):
        raise InvalidSkillNameError("Invalid skill name")
    source = _source(slug)
    content = _scan(source)
    expected = {
        "schema": 1,
        "name": slug,
        "source_identity": hashlib.sha256(str(source).encode()).hexdigest(),
        "content": content,
    }
    parent = _directory(Path(task_dir) / ".deeptutor" / "skills", create=True)
    destination = parent / slug
    if destination.exists() or destination.is_symlink():
        _verify(destination, expected)
        return destination
    staging = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=parent))
    try:
        if _scan(source, copy_to=staging) != content or _scan(source) != content:
            raise SkillExecutionError("Skill package changed during staging")
        if _source(slug) != source:
            raise SkillExecutionError("Skill authorization changed during staging")
        manifest = staging / _MANIFEST
        manifest.write_text(json.dumps(expected, sort_keys=True), encoding="utf-8")
        manifest.chmod(0o444)
        for folder in sorted(staging.rglob("*"), reverse=True):
            if folder.is_dir():
                folder.chmod(0o555)
        # macOS requires a writable source directory for rename. Contents are
        # already sealed; seal the root immediately after the atomic publish.
        _verify(staging, expected, staging=True)
        try:
            staging.rename(destination)
            destination.chmod(0o555)
        except OSError:
            # Another caller may have atomically published the identical snapshot.
            if not destination.exists():
                raise
            _verify(destination, expected)
        return destination
    finally:
        if staging.exists():
            for folder, _, _ in os.walk(staging):
                Path(folder).chmod(0o700)
            shutil.rmtree(staging)
