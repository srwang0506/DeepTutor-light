"""Personal LaTeX project operations on the same files as Studio's editor and build queue."""

import difflib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zipfile

from pydantic import Field
import regex

from . import manuscripts as ms
from .database import Conflict, Missing, encoded, sha
from .models import Command

REF = "refs/heads/deeptutor-writing"
MAX_INDEX = 12_000_000


class Search(Command):
    query: str = Field(min_length=1, max_length=1000)
    replacement: str | None = Field(default=None, max_length=10000)
    case_sensitive: bool = False
    regexp: bool = False
    preview_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def configure(store, mid, main_file, engine, expected):
    with ms.lock(store, mid):
        if (
            not main_file.lower().endswith(".tex")
            or not ms.project_file(store, mid, main_file).is_file()
        ):
            raise ValueError("Choose an existing main TeX file.")
        with store.db(write=True) as db:
            row = store.record(db, mid, "manuscript")
            return store.put(
                db,
                "manuscript",
                {**row["data"], "main_file": main_file, "engine": engine},
                identifier=mid,
                expected=expected,
            )


def source_map(store, mid):
    return {row["path"]: row["hash"] for row in ms.file_list(store, mid)}


def project_index(store, mid):
    with ms.lock(store, mid):
        files = ms.file_list(store, mid)
        main = store.get(mid, "manuscript")["data"]["main_file"]
        sources, omitted, size = {}, [], 0
        for row in sorted(files, key=lambda f: (f["path"] != main, f["path"])):
            if not row["editable"]:
                continue
            name = row["path"]
            if row["size"] > 2_000_000 or size + row["size"] > MAX_INDEX:
                omitted.append(name)
                continue
            try:
                sources[name] = ms.read_bounded(
                    ms.project_file(store, mid, name), 2_000_000
                ).decode()
            except UnicodeError:
                omitted.append(name)
                continue
            size += row["size"]
        return {"files": files, "sources": sources, "omitted": omitted, "main": main}


def archive(store, mid):
    out = io.BytesIO()
    with (
        ms.lock(store, mid),
        zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as package,
    ):
        for row in ms.file_list(store, mid):
            name = row["path"]
            package.writestr(name, ms.read_bounded(ms.project_file(store, mid, name)))
    return out.getvalue()


def _actual(path):
    return sha(ms.read_bounded(path)) if path.exists() else None


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".studio-tmp-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover_batch(store, mid):
    """Caller holds the manuscript lock. A restarted worker rolls a prepared edit forward."""
    journal = ms.tree(store, mid).parent / "pending-editor-edit.json"
    if not journal.is_file():
        return
    operation = json.loads(ms.read_bounded(journal, 2_000_000))
    changes = operation["changes"]
    for name, (before, after) in changes.items():
        if _actual(ms.project_file(store, mid, name)) not in {before, after}:
            raise Conflict(
                "A project file changed during recovery. Its original and prepared copies are preserved."
            )
    for name, (_, after) in changes.items():
        target = ms.project_file(store, mid, name)
        if _actual(target) == after:
            continue
        if after is None:
            target.unlink(missing_ok=True)
        else:
            raw = ms.blob_bytes(store, after)
            if sha(raw) != after:
                raise Conflict("Stored file failed its integrity check.")
            _write(target, raw)
    # A trash record and its undo operation have the same lifetime as their journal.
    if operation.get("trash"):
        with store.db(write=True) as db:
            action = store.record(db, operation["trash"], "manuscript_trash")
            if action["data"]["state"] != operation["state"]:
                store.put(
                    db,
                    action["kind"],
                    {**action["data"], "state": operation["state"]},
                    identifier=action["id"],
                    expected=action["revision"],
                )
    journal.unlink()


def apply_batch(store, mid, changes, *, trash=None, state=None):
    """All comparisons precede the first mutation. None denotes absence, distinct from an empty file."""
    current = source_map(store, mid)
    if any(current.get(name) != old for name, (old, _) in changes.items()):
        raise Conflict("The paper changed. Refresh the preview before applying.")
    updated = dict(current)
    for name, (_, digest) in changes.items():
        ms.relative_path(name)
        if digest is None:
            updated.pop(name, None)
        else:
            updated[name] = digest
    if store.get(mid, "manuscript")["data"]["main_file"] not in updated:
        raise ValueError("Keep the main TeX file in the project.")
    if any(str(parent) in updated for name in updated for parent in Path(name).parents):
        raise ValueError("A project file and a folder cannot share the same path.")
    # Reject folder/file replacements before creating the recovery journal.
    for name in changes:
        path = ms.project_file(store, mid, name)
        if path.is_dir() or any(parent.is_file() for parent in path.parents):
            raise ValueError("Choose an available file path.")
    if len(updated) > 2000:
        raise ValueError("Manuscript exceeds 2,000 files or 200 MB.")
    total = 0
    for name, digest in updated.items():
        if name not in changes:
            total += ms.project_file(store, mid, name).stat().st_size
        elif digest != sha(b""):
            total += store.blob_path(digest).stat().st_size
    if total > 200 * 1024 * 1024:
        raise ValueError("Manuscript exceeds 2,000 files or 200 MB.")
    journal = ms.tree(store, mid).parent / "pending-editor-edit.json"
    _write(journal, encoded({"changes": changes, "trash": trash, "state": state}).encode())
    recover_batch(store, mid)


def upload(store, mid, name, data):
    name = str(ms.relative_path(name))
    if Path(name).suffix.lower() in ms.TEXT_EXTENSIONS:
        if len(data) > 2_000_000:
            raise ValueError("Edit text files up to 2 MB.")
        data.decode()
    with ms.lock(store, mid):
        apply_batch(store, mid, {name: (None, store.save_blob(data))})
    return {"path": name, "hash": sha(data)}


def manage_file(store, mid, name, base_hash, destination=None):
    with ms.lock(store, mid):
        current = source_map(store, mid)
        if current.get(name) != base_hash:
            raise Conflict("The paper changed. Refresh the preview before applying.")
        if name == store.get(mid, "manuscript")["data"]["main_file"]:
            raise ValueError("Keep the main TeX file in the project.")
        old = store.save_blob(ms.read_bounded(ms.project_file(store, mid, name)))
        changes = {name: (base_hash, None)}
        if destination:
            new = str(ms.relative_path(destination))
            if new == name:
                raise ValueError("Choose a different destination.")
            changes[new] = (None, old)
            apply_batch(store, mid, changes)
        else:
            with store.db(write=True) as db:
                item = store.put(
                    db,
                    "manuscript_trash",
                    {"manuscript_id": mid, "path": name, "hash": old, "state": "prepared"},
                )
            apply_batch(store, mid, changes, trash=item["id"], state="deleted")
    return {"moved_to": destination, "path": name}


def trash(store, mid):
    with ms.lock(store, mid):
        recover_batch(store, mid)
        with store.db() as db:
            rows = db.execute(
                "SELECT id FROM records WHERE scope=? AND kind='manuscript_trash' "
                "AND json_extract(data,'$.manuscript_id')=? AND json_extract(data,'$.state')='deleted' "
                "ORDER BY updated DESC",
                (store.project, mid),
            )
            return [store.record(db, row[0]) for row in rows]


def restore_trash(store, mid, item_id):
    with ms.lock(store, mid):
        recover_batch(store, mid)
        row = store.get(item_id, "manuscript_trash")["data"]
        if row["manuscript_id"] != mid or row["state"] != "deleted":
            raise Missing("Deleted file not found.")
        apply_batch(store, mid, {row["path"]: (None, row["hash"])}, trash=item_id, state="restored")
    return {"path": row["path"]}


def search(store, mid, spec, *, apply=False):
    with ms.lock(store, mid):
        sources = ms.file_list(store, mid)
        try:
            pattern = regex.compile(
                spec.query if spec.regexp else regex.escape(spec.query),
                0 if spec.case_sensitive else regex.IGNORECASE,
            )
        except regex.error as exc:
            raise ValueError("Invalid regular expression.") from exc
        result, changes, replacements, hits = [], {}, {}, 0
        deadline = time.monotonic() + 8
        for row in sources:
            if not row["editable"]:
                continue
            name = row["path"]
            raw = ms.read_bounded(ms.project_file(store, mid, name))
            try:
                content = raw.decode()
            except UnicodeError:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Search took too long. Narrow the expression.")
            parts, last, line = [], 0, 1
            try:
                for match in pattern.finditer(content, timeout=min(1.0, remaining)):
                    if hits >= 2000:
                        raise ValueError("More than 2,000 matches. Narrow the expression.")
                    line += content.count("\n", last, match.start())
                    before = match.group()
                    after = (
                        None
                        if spec.replacement is None
                        else (match.expand(spec.replacement) if spec.regexp else spec.replacement)
                    )
                    if len(result) < 200:
                        start = max(content.rfind("\n", 0, match.start()) + 1, match.start() - 55)
                        end = content.find("\n", match.end())
                        result.append(
                            {
                                "path": name,
                                "line": line,
                                "before": before[:280],
                                "after": after[:280] if after is not None else None,
                                "excerpt": content[
                                    start : min(
                                        end if end >= 0 else len(content), match.start() + 200
                                    )
                                ],
                            }
                        )
                    if after is not None:
                        parts.extend((content[last : match.start()], after))
                    line += before.count("\n")
                    last = match.end()
                    hits += 1
            except (regex.error, IndexError, TimeoutError) as exc:
                raise ValueError("Invalid or overly expensive search or replacement.") from exc
            if parts:
                new = ("".join(parts) + content[last:]).encode()
                if len(new) > 2_000_000:
                    raise ValueError("Edit text files up to 2 MB.")
                if sha(new) != row["hash"]:
                    changes[name] = (row["hash"], sha(new))
                    replacements[name] = new
        preview_id = sha(encoded([spec.model_dump(exclude={"preview_id"}), changes]).encode())
        if apply:
            if spec.replacement is None or not spec.preview_id or spec.preview_id != preview_id:
                raise Conflict("The paper changed. Refresh the preview before applying.")
            for raw in replacements.values():
                store.save_blob(raw)
            if changes:
                apply_batch(store, mid, changes)
        return {"matches": result, "total": hits, "files": len(changes), "preview_id": preview_id}


def _git(root, *args, data=None, env=None, accepted=(0,)):
    settings = {
        "PATH": os.defpath if os.name != "nt" else os.environ.get("PATH", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "DeepTutor",
        "GIT_AUTHOR_EMAIL": "writer@localhost",
        "GIT_COMMITTER_NAME": "DeepTutor",
        "GIT_COMMITTER_EMAIL": "writer@localhost",
        **(env or {}),
    }
    call = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "gc.auto=0",
            "-C",
            str(root),
            *args,
        ],
        input=data,
        capture_output=True,
        timeout=35,
        env=settings,
    )
    if call.returncode not in accepted:
        raise ValueError("The manuscript Git operation did not complete.")
    return call


def _head(root):
    return (
        _git(root, "rev-parse", "--verify", "--quiet", REF, accepted=(0, 1)).stdout.decode().strip()
    )


def checkpoint(store, mid, title, *, merge_parent=None):
    if not title.strip():
        raise ValueError("Name this version first.")
    with ms.lock(store, mid):
        files = source_map(store, mid)
        root = ms.tree(store, mid)
        if not (root / ".git").is_dir():
            _git(root, "init", "--quiet")
        previous = _head(root)
        # A private index contains only manuscript files. Recovery journals and unrelated
        # files can never enter a version or a downloaded Git bundle.
        with tempfile.TemporaryDirectory() as folder:
            env = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
            names = b"".join(name.encode() + b"\0" for name in sorted(files))
            _git(
                root,
                "add",
                "--force",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                data=names,
                env=env,
            )
            tree_id = _git(root, "write-tree", env=env).stdout.decode().strip()
        parents = ["-p", previous] if previous else []
        if merge_parent and merge_parent != previous:
            parents.extend(["-p", merge_parent])
        if (
            previous
            and not merge_parent
            and _git(root, "rev-parse", previous + "^{tree}").stdout.decode().strip() == tree_id
        ):
            return {"commit": previous, "created": False}
        commit = (
            _git(root, "commit-tree", tree_id, *parents, "-F", "-", data=title.strip().encode())
            .stdout.decode()
            .strip()
        )
        _git(root, "update-ref", REF, commit, previous or "0" * len(commit))
        return {"commit": commit, "created": True}


def history(store, mid):
    with ms.lock(store, mid):
        root = ms.tree(store, mid)
        if not (root / ".git").is_dir() or not _head(root):
            return []
        rows = (
            _git(root, "log", "--max-count=100", "--format=%H%x00%ct%x00%s", REF)
            .stdout.decode()
            .splitlines()
        )
        return [
            {"commit": rev, "time": int(ts), "title": title}
            for rev, ts, title in (row.split("\0", 2) for row in rows)
        ]


def _valid_commit(root, commit):
    if (
        not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit)
        or _git(root, "merge-base", "--is-ancestor", commit, REF, accepted=(0, 1, 128)).returncode
    ):
        raise Missing("Manuscript version not found.")


def version(store, mid, commit, *, restore_id=None):
    with ms.lock(store, mid):
        root = ms.tree(store, mid)
        _valid_commit(root, commit)
        current = source_map(store, mid)
        target, bodies = {}, {}
        for item in _git(root, "ls-tree", "-r", "-z", commit).stdout.split(b"\0"):
            if not item:
                continue
            meta, name = item.split(b"\t", 1)
            mode, kind, oid = meta.split()
            path = str(ms.relative_path(name.decode()))
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                raise ValueError("Unsupported file in manuscript version.")
            body = _git(root, "cat-file", "blob", oid.decode()).stdout
            target[path] = sha(body)
            if current.get(path) != target[path]:
                bodies[path] = body
        changes = {
            name: (current.get(name), target.get(name))
            for name in sorted(current.keys() | target.keys())
            if current.get(name) != target.get(name)
        }
        guard = sha(encoded([commit, current]).encode())
        if restore_id is not None:
            if restore_id != guard:
                raise Conflict("The paper changed. Refresh the preview before applying.")
            if changes:
                checkpoint(store, mid, "Before restore · " + commit[:10])
                for data in bodies.values():
                    store.save_blob(data)
                apply_batch(store, mid, changes)
            return {"restored": True, "files": len(changes)}
        files = []
        for name, (old, new) in changes.items():
            diff = ""
            if Path(name).suffix.lower() in ms.TEXT_EXTENSIONS and len(files) < 25:
                before = (
                    ms.read_bounded(ms.project_file(store, mid, name)) if old is not None else b""
                )
                after = bodies.get(name, b"")
                if max(len(before), len(after)) < 100_000:
                    lines = difflib.unified_diff(
                        before.decode(errors="replace").splitlines(),
                        after.decode(errors="replace").splitlines(),
                        lineterm="",
                    )
                    import itertools

                    diff = "\n".join(itertools.islice(lines, 140))[:12000]
            files.append(
                {
                    "path": name,
                    "status": "add" if old is None else "remove" if new is None else "modify",
                    "diff": diff,
                }
            )
        return {"commit": commit, "files": files, "restore_id": guard}


def git_bundle(store, mid):
    with ms.lock(store, mid), tempfile.TemporaryDirectory() as folder:
        current = checkpoint(store, mid, "Source export")["commit"]
        root = ms.tree(store, mid)
        _git(folder, "init", "--bare", "--initial-branch=main", "--quiet")
        env = {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(root / ".git" / "objects")}
        _git(folder, "update-ref", "refs/heads/main", current, env=env)
        path = Path(folder) / "paper.bundle"
        _git(folder, "bundle", "create", str(path), "HEAD", "refs/heads/main", env=env)
        return path.read_bytes()


def _bundle_tree(folder, revision, env):
    """Inspect Git objects without checking out the archive or executing project code."""
    entries = _git(folder, "ls-tree", "-lr", "-z", revision, env=env).stdout.split(b"\0")
    if len(entries) > 2001:
        raise ValueError("Manuscript exceeds 2,000 files or 200 MB.")
    files, total = {}, 0
    for item in entries:
        if not item:
            continue
        meta, name = item.split(b"\t", 1)
        mode, kind, oid, size = meta.split()
        path = str(ms.relative_path(name.decode()))
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("Unsupported file in manuscript version.")
        total += int(size)
        if total > 200 * 1024 * 1024 or int(size) > 100 * 1024 * 1024:
            raise ValueError("Manuscript exceeds 2,000 files or 200 MB.")
        files[path] = oid.decode()
    return files


def import_bundle(store, mid, data, preview_id=None):
    with ms.lock(store, mid), tempfile.TemporaryDirectory() as temp:
        root = ms.tree(store, mid)
        head = _head(root) if (root / ".git").is_dir() else ""
        if not head:
            raise ValueError("Start from a Git bundle exported by this manuscript.")
        env = {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(root / ".git" / "objects")}
        folder = Path(temp)
        _git(folder, "init", "--bare", "--initial-branch=main", "--quiet")
        _git(folder, "update-ref", "refs/heads/base", head, env=env)
        incoming_file = folder / "changes.bundle"
        incoming_file.write_bytes(data)
        list_heads = (
            _git(folder, "bundle", "list-heads", str(incoming_file), env=env)
            .stdout.decode()
            .splitlines()
        )
        refs = {line.split(" ", 1)[1] for line in list_heads if " " in line}
        selected = next((ref for ref in ["HEAD", "refs/heads/main", REF] if ref in refs), None)
        if not selected:
            raise ValueError("Export a Git bundle with HEAD or the main branch.")
        _git(
            folder,
            "-c",
            "fetch.fsckObjects=true",
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            str(incoming_file),
            selected + ":refs/heads/incoming",
            env=env,
        )
        incoming = _git(folder, "rev-parse", "refs/heads/incoming", env=env).stdout.decode().strip()
        base = (
            _git(folder, "merge-base", head, incoming, env=env, accepted=(0, 1))
            .stdout.decode()
            .strip()
        )
        if not base:
            raise ValueError("Start from a Git bundle exported by this manuscript.")
        old, new = _bundle_tree(folder, base, env), _bundle_tree(folder, incoming, env)
        current = source_map(store, mid)
        changes, contents, conflicts, files = {}, {}, [], []
        for name in sorted(old.keys() | new.keys()):
            if old.get(name) == new.get(name):
                continue
            before = (
                sha(_git(folder, "cat-file", "blob", old[name], env=env).stdout)
                if name in old
                else None
            )
            raw = (
                _git(folder, "cat-file", "blob", new[name], env=env).stdout if name in new else None
            )
            after = sha(raw) if raw is not None else None
            if after == current.get(name):
                continue
            if before != current.get(name):
                conflicts.append(name)
            changes[name] = (before, after)
            if raw is not None:
                if Path(name).suffix.lower() in ms.TEXT_EXTENSIONS:
                    if len(raw) > 2_000_000:
                        raise ValueError("Edit text files up to 2 MB.")
                    raw.decode()
                contents[name] = new[name]
            files.append(name)
        if store.get(mid, "manuscript")["data"]["main_file"] not in new:
            raise ValueError("Keep the main TeX file in the project.")
        guard = sha(encoded([incoming, head, current]).encode())
        if preview_id is not None:
            if conflicts or preview_id != guard:
                raise Conflict("The paper changed. Refresh the preview before applying.")
            is_ancestor = (
                _git(
                    folder, "merge-base", "--is-ancestor", incoming, head, env=env, accepted=(0, 1)
                ).returncode
                == 0
            )
            if changes or not is_ancestor:
                checkpoint(store, mid, "Before Git import")
                for name, oid in contents.items():
                    store.save_blob(_git(folder, "cat-file", "blob", oid, env=env).stdout)
                # Transfer the inspected history as objects only. All worktree edits go
                # through the same durable, exact-base operation as a browser edit.
                _git(
                    root,
                    "-c",
                    "fetch.fsckObjects=true",
                    "fetch",
                    "--no-tags",
                    "--no-recurse-submodules",
                    str(incoming_file),
                    selected + ":refs/deeptutor/imports/" + incoming,
                )
                if changes:
                    apply_batch(store, mid, changes)
                checkpoint(store, mid, "Git import · " + incoming[:10], merge_parent=incoming)
        return {"commit": incoming, "files": files, "conflicts": conflicts, "preview_id": guard}


def blame(store, mid, name):
    path = str(ms.relative_path(name))
    with ms.lock(store, mid):
        root = ms.tree(store, mid)
        commit = _head(root) if (root / ".git").is_dir() else ""
        if (
            not commit
            or _git(root, "cat-file", "-e", commit + ":" + path, accepted=(0, 128)).returncode
        ):
            return {"commit": None, "lines": []}
        raw = _git(root, "blame", "--line-porcelain", commit, "--", path).stdout.decode(
            errors="replace"
        )
        entries = []
        for line in raw.splitlines():
            header = re.fullmatch(r"([a-f0-9]{40,64}) \d+ (\d+)(?: \d+)?", line)
            if header:
                entries.append({"commit": header[1], "line": int(header[2])})
            elif entries and line.startswith("summary "):
                entries[-1]["title"] = line[8:]
        return {"commit": commit, "lines": entries}
