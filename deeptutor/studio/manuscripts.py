"""Real TeX files, exact-base edits, isolated builds and immutable submissions."""

import difflib
from functools import lru_cache
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from uuid import uuid4
import zipfile

from filelock import FileLock

from .database import Conflict, Missing, encoded, sha
from .models import FileEdit, ManuscriptInput, ProposalInput, ReviewInput

SOURCE_EXTENSIONS = {
    ".tex",
    ".bib",
    ".sty",
    ".cls",
    ".bst",
    ".bbx",
    ".cbx",
    ".def",
    ".cfg",
    ".txt",
    ".md",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".eps",
    ".csv",
}
TEXT_EXTENSIONS = SOURCE_EXTENSIONS - {".pdf", ".png", ".jpg", ".jpeg", ".eps"}


def relative_path(value):
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(p in {".", ".."} or p.startswith(".") for p in path.parts)
    ):
        raise ValueError("Use a relative, nonhidden project file path.")
    if path.suffix.lower() not in SOURCE_EXTENSIONS:
        raise ValueError("Unsupported manuscript file type.")
    return path


def tree(store, identifier):
    store.get(identifier, "manuscript")
    root = store.root / "manuscripts" / identifier / "tree"
    if root.is_symlink():
        raise ValueError("Manuscript roots cannot be symbolic links.")
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_file(store, identifier, path):
    root = tree(store, identifier)
    result = root / str(relative_path(path))
    if not result.resolve().is_relative_to(root.resolve()) or any(
        p.is_symlink() for p in [result, *result.parents] if p != root.parent
    ):
        raise ValueError("Linked files are not permitted in manuscript projects.")
    return result


def lock(store, identifier):
    root = tree(store, identifier).parent
    return _file_lock(str(root / ".files.lock"))


@lru_cache(maxsize=256)
def _file_lock(path):
    return FileLock(path, timeout=10)


def project_entries(root):
    """Avoid hidden Git internals, symlinks, and unbounded directory walks."""
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        if len(dirs) + len(files) > 5000:
            raise ValueError("Project directory contains too many entries.")
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in [*dirs, *files]:
            count += 1
            if count > 10000:
                raise ValueError("Project contains too many entries.")
            candidate = Path(directory) / name
            if candidate.is_symlink():
                raise ValueError("Project contains a symbolic link.")
        for name in files:
            yield Path(directory) / name


def read_bounded(path, maximum=100 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("File is not regular or exceeds the supported size.")
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("File exceeds the supported size.")
    return data


def blob_bytes(store, digest):
    return b"" if digest == sha(b"") else store.blob_path(digest).read_bytes()


def recover_saves(store, identifier):
    """Reconcile prepared saves after a crash without overwriting either version."""
    unresolved = []
    with lock(store, identifier):
        root = tree(store, identifier)
        for journal in sorted(project_entries(root)):
            if not re.fullmatch(r"\.studio-save-[a-f0-9]+\.json", journal.name):
                continue
            name = journal.relative_to(root).as_posix()
            try:
                raw = read_bounded(journal, 2_000_000)
                data = json.loads(raw)
                if not all(re.fullmatch(r"[a-f0-9]{64}", data[k]) for k in ("before", "after")):
                    raise ValueError("Invalid save hash.")
                target = project_file(store, identifier, data["path"])
                actual = sha(read_bounded(target)) if target.exists() else sha(b"")
            except (ValueError, KeyError, TypeError, OSError) as exc:
                unresolved.append(
                    {"journal": name, "path": None, "error": str(exc), "invalid": True}
                )
                continue
            if actual not in {data["before"], data["after"]}:
                unresolved.append(
                    {
                        "journal": name,
                        "path": data["path"],
                        "before": data["before"],
                        "after": data["after"],
                        "disk": actual,
                        "before_text": blob_bytes(store, data["before"]).decode("utf-8"),
                        "after_text": blob_bytes(store, data["after"]).decode("utf-8"),
                        "disk_text": read_bounded(target, 2_000_000).decode("utf-8"),
                    }
                )
                continue
            with store.db(write=True) as db:
                found = db.execute(
                    "SELECT 1 FROM records WHERE kind='file_change' AND scope=? "
                    "AND json_extract(data,'$.manuscript_id')=? AND json_extract(data,'$.path')=? "
                    "AND json_extract(data,'$.before')=? AND json_extract(data,'$.after')=?",
                    (store.project, identifier, data["path"], data["before"], data["after"]),
                ).fetchone()
                if not found and actual == data["after"]:
                    store.put(
                        db, "file_change", {**data, "manuscript_id": identifier, "recovered": True}
                    )
                    row = store.record(db, identifier, "manuscript")
                    store.put(
                        db,
                        "manuscript",
                        row["data"],
                        identifier=identifier,
                        expected=row["revision"],
                    )
                if actual == data["after"] and data.get("proposal"):
                    _finalize_proposal(store, db, identifier, data["proposal"], data["after"], None)
            journal.unlink()
    return unresolved


def resolve_recovery(store, identifier, journal_name, disk_hash, choice):
    if choice not in {"disk", "before", "prepared"}:
        raise ValueError("Choose a recovery version.")
    with lock(store, identifier):
        conflict = next(
            (c for c in recover_saves(store, identifier) if c["journal"] == journal_name), None
        )
        if not conflict:
            raise Conflict("This recovery item changed. Reload before resolving it.")
        journal = tree(store, identifier) / conflict["journal"]
        if conflict.get("invalid"):
            if choice != "disk":
                raise ValueError(
                    "An invalid save journal can only be archived while keeping disk files."
                )
            selected = None
        else:
            if disk_hash != conflict["disk"]:
                raise Conflict("The disk file changed. Review the current versions.")
            disk = read_bounded(project_file(store, identifier, conflict["path"]), 2_000_000)
            selected = conflict[{"disk": "disk", "before": "before", "prepared": "after"}[choice]]
            if disk:
                store.save_blob(disk)
            if choice != "disk":
                _save_locked(
                    store,
                    identifier,
                    FileEdit(
                        path=conflict["path"],
                        base_hash=disk_hash,
                        text=blob_bytes(store, selected).decode("utf-8"),
                    ),
                    skip_recovery=True,
                )
        raw = read_bounded(journal, 2_000_000)
        journal_hash = store.save_blob(raw) if raw else sha(b"")
        with store.db(write=True) as db:
            recovery = store.put(
                db,
                "recovery",
                {
                    "manuscript_id": identifier,
                    "path": conflict.get("path"),
                    "journal_hash": journal_hash,
                    "choice": choice,
                    "selected_hash": selected,
                    "disk_hash": disk_hash,
                    "resolved": time.time(),
                },
            )
            prepared = {} if conflict.get("invalid") else json.loads(raw)
            if selected == prepared.get("after") and prepared.get("proposal"):
                _finalize_proposal(
                    store, db, identifier, prepared["proposal"], selected, recovery["id"]
                )
        journal.unlink()
        return {"resolved": True, "choice": choice, "hash": selected}


def file_list(store, identifier):
    recover_saves(store, identifier)
    root = tree(store, identifier)
    files, size = [], 0
    for p in sorted(project_entries(root)):
        relative = p.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if p.suffix.lower() in SOURCE_EXTENSIONS:
            data = read_bounded(p)
            size += len(data)
            if len(files) >= 2000 or size > 200 * 1024 * 1024:
                raise ValueError("Manuscript exceeds 2,000 files or 200 MB.")
            files.append(
                {
                    "path": relative.as_posix(),
                    "hash": sha(data),
                    "size": len(data),
                    "editable": p.suffix.lower() in TEXT_EXTENSIONS,
                }
            )
    return files


def create_manuscript(store, command: ManuscriptInput, data: bytes | None = None):
    relative_path(command.main_file)
    files = {}
    if data:
        if len(data) > 100 * 1024 * 1024:
            raise ValueError("Project archive exceeds 100 MB.")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [
                i
                for i in archive.infolist()
                if not i.is_dir() and not i.filename.startswith("__MACOSX/")
            ]
            if len(entries) > 2000 or sum(i.file_size for i in entries) > 200 * 1024 * 1024:
                raise ValueError("Expanded project exceeds the import limit.")
            for item in entries:
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("Archive contains symbolic links.")
                name = str(relative_path(item.filename))
                if name in files:
                    raise ValueError("Archive contains duplicate file paths.")
                files[name] = archive.read(item)
        if command.main_file not in files:
            raise ValueError("Choose the main TeX file present in this archive.")
    else:
        files[command.main_file] = (
            b"\\documentclass{article}\n\\usepackage{graphicx}\n\\title{Research notes}\n\\begin{document}\n\\maketitle\n\\section{Introduction}\nWrite your evidence-grounded argument here.\n\\end{document}\n"
        )
    with store.db(write=True) as db:
        row = store.put(db, "manuscript", {**command.model_dump(), "status": "ready"})
    root = tree(store, row["id"])
    try:
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        subprocess.run(
            ["git", "init", "--quiet", str(root)], check=True, capture_output=True, timeout=15
        )
    except Exception:
        with store.db(write=True) as db:
            store.put(
                db,
                "manuscript",
                {**row["data"], "status": "import_failed"},
                identifier=row["id"],
                expected=row["revision"],
            )
        raise
    return {**row, "files": file_list(store, row["id"])}


def read_file(store, identifier, path):
    recover_saves(store, identifier)
    file = project_file(store, identifier, path)
    if not file.is_file() or file.suffix.lower() not in TEXT_EXTENSIONS:
        raise Missing("Text file not found.")
    data = read_bounded(file, 2_000_000)
    if len(data) > 2_000_000:
        raise ValueError("Edit text files up to 2 MB.")
    return {"path": path, "text": data.decode("utf-8"), "hash": sha(data)}


def _save_locked(store, identifier, command: FileEdit, skip_recovery=False, proposal=None):
    if not skip_recovery and any(
        item["path"] in {None, command.path} for item in recover_saves(store, identifier)
    ):
        raise Conflict(
            "A recovered save conflicts with an external edit. Review recovery versions before saving."
        )
    file = project_file(store, identifier, command.path)
    if file.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError("This file is not editable text.")
    old = read_bounded(file, 2_000_000) if file.exists() else b""
    if sha(old) != command.base_hash:
        raise Conflict("The file changed in another editor. Compare before saving.")
    content = command.text.encode()
    old_hash = store.save_blob(old) if old else sha(b"")
    new_hash = store.save_blob(content) if content else sha(b"")
    file.parent.mkdir(parents=True, exist_ok=True)
    # A durable prepared journal preserves both sides if the process stops between file and DB writes.
    journal = file.parent / (".studio-save-" + uuid4().hex + ".json")
    with journal.open("w") as handle:
        handle.write(
            encoded(
                {"path": command.path, "before": old_hash, "after": new_hash, "proposal": proposal}
            )
        )
        handle.flush()
        os.fsync(handle.fileno())
    temporary = file.parent / (".studio-write-" + uuid4().hex)
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, file)
    with store.db(write=True) as db:
        manuscript = store.record(db, identifier, "manuscript")
        change = store.put(
            db,
            "file_change",
            {
                "manuscript_id": identifier,
                "path": command.path,
                "before": old_hash,
                "after": new_hash,
            },
        )
        store.put(
            db,
            "manuscript",
            manuscript["data"],
            identifier=identifier,
            expected=manuscript["revision"],
        )
        if proposal:
            _finalize_proposal(store, db, identifier, proposal, new_hash, change["id"])
    journal.unlink()
    return {"path": command.path, "hash": new_hash, "change_id": change["id"]}


def save_file(store, identifier, command: FileEdit):
    with lock(store, identifier):
        return _save_locked(store, identifier, command)


def factual_changes(before, after):
    patterns = {
        "numbers_units": r"(?<![\w.])[+\-−]?\d+(?:\.\d+)?(?:[eE][+\-−]?\d+)?(?:%|\s*(?:[numkMGTμµ]?W|mm|cm|ms|m|s|Hz|GB)\b)?",
        "citations": r"\\(?:cite\w*|ref|eqref)\*?(?:\[[^\]]*\])*\{[^}]+\}",
        "equations": r"\$[^$]+\$|\\\[[\s\S]*?\\\]",
        "negation": r"\b(?:not|never|without|no)\b|不|未|无",
        "scope": r"\b(?:only|all|every|seen|unseen|some|certain|always)\b|仅|所有|全部|已见|未见|部分|始终",
        "causality": r"\b(?:cause[sd]?|prove[sd]?|suggest[sd]?|correlat\w*|guarantee[sd]?)\b|导致|证明|表明|相关|保证",
        "comparison": r"\b[\w-]+\s+(?:(?:consistently|significantly|slightly)\s+)?(?:outperforms?|underperforms?|exceeds?|surpasses?)\s+[\w-]+\b|\b[\w-]+\s+(?:increase[sd]?|decrease[sd]?|improve[sd]?|reduce[sd]?)\b|\b(?:higher|lower|better|worse)\s+than\s+[\w-]+\b|[\w]+(?:优于|劣于|高于|低于|增加|降低|减少|提升)[\w]*",
    }
    return [
        {
            "kind": kind,
            "before": re.findall(pattern, before, 0 if kind == "numbers_units" else re.I),
            "after": re.findall(pattern, after, 0 if kind == "numbers_units" else re.I),
        }
        for kind, pattern in patterns.items()
        if re.findall(pattern, before, 0 if kind == "numbers_units" else re.I)
        != re.findall(pattern, after, 0 if kind == "numbers_units" else re.I)
    ]


def propose(store, identifier, command: ProposalInput, *, read_set=None):
    current = read_file(store, identifier, command.path)
    if current["hash"] != command.base_hash:
        raise Conflict("Reload this file before proposing a change.")
    if not 0 <= command.start <= command.end <= len(current["text"]):
        raise ValueError("Selection is outside the file.")
    before = current["text"][command.start : command.end]
    from .read_set import ReadSet

    read_set = read_set or ReadSet.capture(store, command.source_ids, rules={"writing_context": 1})
    payload = {
        **command.model_dump(),
        "read_set": read_set.manifest(),
        "manuscript_id": identifier,
        "before": before,
        "status": "pending",
        "fact_changes": factual_changes(before, command.text),
        "diff": "".join(
            difflib.unified_diff(
                [line + "\n" for line in before.splitlines()],
                [line + "\n" for line in command.text.splitlines()],
                fromfile="original",
                tofile="proposal",
            )
        ),
    }
    with store.db(write=True) as db:
        for source in command.source_ids:
            row = store.record(db, source)
            if (
                source in command.source_revisions
                and row["revision"] != command.source_revisions[source]
            ):
                raise Conflict(
                    "A writing source changed during generation. Recheck the evidence and regenerate."
                )
        row = store.put(db, "proposal", payload)
        for source in command.source_ids:
            store.link(
                db,
                source,
                row["id"],
                "evidence_for_edit",
                source_revision=read_set.get(source)["revision"],
            )
        return row


def _finalize_proposal(store, db, manuscript, operation, applied_hash, change_id):
    proposal = store.record(db, operation["id"], "proposal")
    data = proposal["data"]
    if data.get("status") == "accepted" and data.get("applied_hash") == applied_hash:
        return proposal
    if (
        proposal["revision"] != operation["revision"]
        or data.get("status") != "pending"
        or data["manuscript_id"] != manuscript
    ):
        raise Conflict(
            "Prepared edit no longer matches its proposal; keep the journal for recovery."
        )
    row = store.put(
        db,
        "proposal",
        {
            **data,
            "status": "accepted",
            "applied_hash": applied_hash,
            "receipt": {
                "operation_id": operation["id"],
                "state": "applied",
                "hash": applied_hash,
                "change_id": change_id,
            },
        },
        identifier=proposal["id"],
        expected=proposal["revision"],
    )
    for source in data["source_ids"]:
        consumed = db.execute(
            "SELECT source_revision FROM dependencies WHERE source=? AND target=? AND relation='evidence_for_edit'",
            (source, proposal["id"]),
        ).fetchone()
        if consumed is None:
            raise Conflict("The proposal's original evidence revision is unavailable.")
        store.link(db, source, manuscript, "manuscript_evidence", source_revision=consumed[0])
    return row


def accept_proposal(store, identifier, expected, confirm_facts=False):
    initial = store.get(identifier, "proposal")
    manuscript = initial["data"]["manuscript_id"]
    with lock(store, manuscript):
        recover_saves(store, manuscript)
        proposal = store.get(identifier, "proposal")
        data = proposal["data"]
        if (
            data["status"] == "accepted"
            and data.get("receipt")
            and proposal["revision"] == expected + 1
        ):
            return proposal  # Same operation retry returns the durable receipt.
        if proposal["revision"] != expected or data["status"] != "pending":
            raise Conflict("This suggestion is no longer pending.")
        if store.stale(identifier):
            raise Conflict(
                "The evidence behind this suggestion changed. Review it and generate a new suggestion."
            )
        if data["fact_changes"] and not confirm_facts:
            raise ValueError("Review factual changes before accepting this edit.")
        current = read_file(store, manuscript, data["path"])
        if current["hash"] != data["base_hash"]:
            raise Conflict("The file changed since this proposal. No edit was applied.")
        _save_locked(
            store,
            manuscript,
            FileEdit(
                path=data["path"],
                base_hash=data["base_hash"],
                text=current["text"][: data["start"]]
                + data["text"]
                + current["text"][data["end"] :],
            ),
            proposal={"id": identifier, "revision": expected},
        )
        return store.get(identifier, "proposal")


def audit(store, identifier):
    files = file_list(store, identifier)
    citations = set()
    labels = []
    references = set()
    keys = []
    warnings = []
    for item in files:
        path = item["path"]
        if path.endswith(".tex"):
            text = read_file(store, identifier, path)["text"]
            citations.update(
                k.strip()
                for group in re.findall(r"\\cite\w*\*?(?:\[[^\]]*\])*\{([^}]+)\}", text)
                for k in group.split(",")
            )
            labels.extend(re.findall(r"\\label\{([^}]+)\}", text))
            references.update(re.findall(r"\\(?:ref|eqref|autoref|cref)\{([^}]+)\}", text))
            for match in re.finditer(
                r"\\(?:author|affiliation|thanks)\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text
            ):
                warnings.append(
                    {
                        "path": path,
                        "line": text.count("\n", 0, match.start()) + 1,
                        "kind": "anonymity_review",
                        "excerpt": match.group(),
                    }
                )
        elif path.endswith(".bib"):
            from .catalog import parse_bibliography

            keys.extend(
                r["citekey"]
                for r in parse_bibliography(read_file(store, identifier, path)["text"], "bibtex")
            )
    return {
        "missing_citations": sorted(citations - set(keys)),
        "duplicate_keys": sorted({k for k in keys if keys.count(k) > 1}),
        "unresolved_labels": sorted(references - set(labels)),
        "duplicate_labels": sorted({k for k in labels if labels.count(k) > 1}),
        "warnings": warnings,
        "stale_dependencies": store.stale(identifier),
        "limitations": "Static checks do not establish whether the cited source supports a claim.",
    }


def prepare_build(store, identifier):
    manuscript = store.get(identifier, "manuscript")
    with lock(store, identifier):
        if recover_saves(store, identifier):
            raise Conflict("Resolve recovered save conflicts before compiling.")
        files = file_list(store, identifier)
        snapshots = {}
        for f in files:
            content = read_bounded(project_file(store, identifier, f["path"]))
            snapshots[f["path"]] = store.save_blob(content) if content else sha(b"")
    with store.db(write=True) as db:
        return store.put(
            db,
            "build",
            {
                "manuscript_id": identifier,
                "status": "queued",
                "input_files": snapshots,
                "started": time.time(),
                "engine": manuscript["data"]["engine"],
                "main_file": manuscript["data"]["main_file"],
                "cancel_requested": False,
            },
        )


def compile_project(store, identifier):
    """Synchronous domain entry point for tests and command-line callers."""
    return execute_build(store, prepare_build(store, identifier))


def execute_build(store, build, cancelled=None):
    from filelock import Timeout

    from .builds import lease

    try:
        with lease(store, build["id"]):
            with store.db(write=True) as db:
                current = store.record(db, build["id"], "build")
                if current["data"]["status"] != "queued":
                    return current
                build = store.put(
                    db,
                    "build",
                    {**current["data"], "status": "running", "worker_started": time.time()},
                    identifier=build["id"],
                    expected=current["revision"],
                )
            try:
                return _execute_build(store, build, cancelled)
            except Exception as exc:
                # Keep completion/failure under the same OS lease, including unexpected
                # errors. A stale worker must never overwrite another owner's result.
                digest = store.save_blob(
                    f"Build worker failed: {type(exc).__name__}: {exc}".encode()
                )
                with store.db(write=True) as db:
                    current = store.record(db, build["id"], "build")
                    return store.put(
                        db,
                        "build",
                        {
                            **current["data"],
                            "status": "failed",
                            "finished": time.time(),
                            "outputs": {"build.log": digest},
                        },
                        identifier=build["id"],
                        expected=current["revision"],
                    )
    except Timeout:
        return store.get(build["id"], "build")


def _execute_build(store, build, cancelled=None):
    from .connections import ProcessCancelled

    snapshots = build["data"]["input_files"]
    compiler = shutil.which("latexmk")
    try:
        if cancelled and cancelled():
            raise ProcessCancelled("Build cancelled before compilation.")
        if not compiler:
            raise ValueError(
                "Isolated TeX compilation is unavailable. Install latexmk and an OS sandbox; source editing remains available."
            )
        with tempfile.TemporaryDirectory(prefix="deeptutor-tex-") as tmp:
            root = Path(tmp).resolve()
            source_root = str(root)
            for path, digest in snapshots.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob_bytes(store, digest))
            from .tex_sandbox import distribution_roots

            tex_root = distribution_roots(compiler)
            engine = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[
                build["data"]["engine"]
            ]
            env = {
                "PATH": str(Path(compiler).parent) + ":/usr/bin:/bin",
                "HOME": str(root),
                "TMPDIR": str(root),
                "openin_any": "p",
                "openout_any": "p",
                "shell_escape": "f",
                "TEXMFVAR": str(root / ".tex-cache"),
                "TEXMFCONFIG": str(root / ".tex-config"),
                "SOURCE_DATE_EPOCH": "1700000000",
                "LC_ALL": "C",
                "LANG": "C",
            }
            pdf = root / Path(build["data"]["main_file"]).with_suffix(".pdf").name
            pdf.unlink(missing_ok=True)
            import sys

            from .connections import bounded_process
            from .tex_sandbox import command as sandbox_command

            returncode, stdout, stderr = bounded_process(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import os,resource,sys;resource.setrlimit(resource.RLIMIT_FSIZE,(52428800,52428800));os.execv(sys.argv[1],sys.argv[1:])",
                ]
                + sandbox_command(root, compiler, engine, build["data"]["main_file"], tex_root),
                cwd=root,
                env=env,
                timeout=90,
                limit=2_000_000,
                cancelled=cancelled,
            )
            log = stdout + stderr
            outputs = {
                "build.log": store.save_blob(
                    log or f"Compiler exited with code {returncode} without output.".encode()
                )
            }
            if returncode == 0 and pdf.exists():
                outputs["preview.pdf"] = store.save_blob(pdf.read_bytes())
                synctex = pdf.with_suffix(".synctex.gz")
                if synctex.exists():
                    outputs["mapping.synctex.gz"] = store.save_blob(synctex.read_bytes())
            status = "succeeded" if "preview.pdf" in outputs else "failed"
    except ProcessCancelled as exc:
        outputs = {"build.log": store.save_blob(str(exc).encode())}
        status = "cancelled"
    except (OSError, subprocess.SubprocessError, ValueError, TimeoutError) as exc:
        outputs = {"build.log": store.save_blob(str(exc).encode())}
        status = "failed"
    with store.db(write=True) as db:
        latest = store.record(db, build["id"], "build")
        if latest["data"].get("cancel_requested"):
            status = "cancelled"
            outputs.pop("preview.pdf", None)
            outputs.pop("mapping.synctex.gz", None)
        return store.put(
            db,
            "build",
            {
                **latest["data"],
                "status": status,
                "outputs": outputs,
                "finished": time.time(),
                "source_root": locals().get("source_root"),
            },
            identifier=build["id"],
            expected=latest["revision"],
        )


def dependency_manifest(store, snapshots, main_file):
    """Resolve literal TeX dependencies from frozen bytes; clean compilation checks dynamic ones."""
    if main_file not in snapshots:
        raise ValueError("The build has no main TeX source.")
    required, pending = set(), [main_file]
    reasons, unresolved = {main_file: ["Main TeX source"]}, []
    extensions = {
        "input": ["", ".tex"],
        "include": ["", ".tex"],
        "includegraphics": ["", ".pdf", ".png", ".jpg", ".jpeg", ".eps"],
        "bibliography": ["", ".bib"],
        "addbibresource": ["", ".bib"],
        "usepackage": [".sty"],
        "documentclass": [".cls"],
        "bibliographystyle": [".bst"],
    }
    while pending:
        path = pending.pop()
        if path in required:
            continue
        required.add(path)
        if Path(path).suffix not in {".tex", ".sty", ".cls"}:
            continue
        text = blob_bytes(store, snapshots[path]).decode("utf-8")
        text = re.sub(r"(?<!\\)%[^\n]*", "", text)
        graphics = re.findall(r"\\graphicspath\{((?:\{[^}]*\})+)\}", text)
        roots = ["", str(Path(path).parent)] + [
            v for group in graphics for v in re.findall(r"\{([^}]*)\}", group)
        ]
        for command, names in re.findall(
            r"\\(input|include|includegraphics|bibliography|addbibresource|usepackage|documentclass|bibliographystyle)\*?(?:\[[^\]]*\])*\{([^}]+)\}",
            text,
        ):
            for name in names.split(","):
                name = name.strip()
                if "\\" in name or "#" in name:
                    unresolved.append({"path": path, "command": command, "expression": name})
                    continue
                candidates = []
                for root in roots:
                    for ext in extensions[command]:
                        candidate = str(Path(root) / (name + ext))
                        # A suffixless candidate is legitimate TeX syntax, not a file
                        # to reject before trying .tex/.bib or graphic extensions.
                        if Path(candidate).suffix.lower() not in SOURCE_EXTENSIONS:
                            continue
                        candidates.append(str(relative_path(candidate)))
                found = next(
                    (candidate for candidate in candidates if candidate in snapshots), None
                )
                if found:
                    pending.append(found)
                    reasons.setdefault(found, []).append(f"{path}: \\{command}{{{name}}}")
                elif command not in {"usepackage", "documentclass", "bibliographystyle"}:
                    raise ValueError(f"Missing source dependency: {name}")
    return {"required": sorted(required), "reasons": reasons, "unresolved": unresolved}


def dependency_closure(store, snapshots, main_file):
    return set(dependency_manifest(store, snapshots, main_file)["required"])


def submission_plan(store, identifier, build_id):
    build = store.get(build_id, "build")
    if build["data"]["manuscript_id"] != identifier or build["data"]["status"] != "succeeded":
        raise ValueError("Choose a successful build of this manuscript.")
    main = (
        build["data"].get("main_file") or store.get(identifier, "manuscript")["data"]["main_file"]
    )
    return {
        "build_id": build_id,
        "files": sorted(build["data"]["input_files"]),
        **dependency_manifest(store, build["data"]["input_files"], main),
    }


def submission(store, identifier, paths, build_id, kind="submission", confirm_dynamic=False):
    if not paths or len(paths) > 2000 or len(set(paths)) != len(paths):
        raise ValueError("Choose an explicit, distinct file allowlist.")
    build = store.get(build_id, "build")
    if build["data"]["manuscript_id"] != identifier or build["data"]["status"] != "succeeded":
        raise ValueError("Choose a successful build of this manuscript.")
    snapshots = build["data"]["input_files"]
    if any(p not in snapshots for p in paths):
        raise ValueError("Submission files must come from the selected build snapshot.")
    if kind not in {"submission", "archive"}:
        raise ValueError("Choose submission or internal archive.")
    validation = None
    if kind == "submission":
        main = (
            build["data"].get("main_file")
            or store.get(identifier, "manuscript")["data"]["main_file"]
        )
        if main not in paths:
            raise ValueError("A submission must include its main TeX source.")
        if any(
            Path(p).suffix.lower()
            not in {
                ".tex",
                ".bib",
                ".sty",
                ".cls",
                ".bst",
                ".bbl",
                ".pdf",
                ".png",
                ".jpg",
                ".jpeg",
                ".eps",
                ".svg",
                ".txt",
            }
            for p in paths
        ):
            raise ValueError("Use internal archive for notes, configuration or research logs.")
        manifest = dependency_manifest(store, snapshots, main)
        if manifest["unresolved"] and not confirm_dynamic:
            raise ValueError(
                "Dynamic TeX dependencies need explicit file selection and confirmation before export."
            )
        paths = sorted(set(paths) | set(manifest["required"]))
        with store.db(write=True) as db:
            validation = store.put(
                db,
                "build",
                {
                    **build["data"],
                    "input_files": {p: snapshots[p] for p in paths},
                    "status": "queued",
                    "purpose": "submission_validation",
                    "main_file": main,
                    "engine": build["data"].get(
                        "engine", store.record(db, identifier, "manuscript")["data"]["engine"]
                    ),
                    "started": time.time(),
                    "outputs": {},
                    "cancel_requested": False,
                },
            )
        validation = execute_build(store, validation)
        if validation["data"]["status"] != "succeeded":
            raise ValueError(
                f"Clean submission compilation failed. Inspect build {validation['id']} before exporting."
            )
    pdf_build = validation or build
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.writestr(str(relative_path(path)), blob_bytes(store, snapshots[path]))
        archive.writestr(
            "submission.pdf" if kind == "submission" else "preview.pdf",
            store.blob_path(pdf_build["data"]["outputs"]["preview.pdf"]).read_bytes(),
        )
    digest = store.save_blob(content.getvalue())
    with store.db(write=True) as db:
        return store.put(
            db,
            "submission",
            {
                "manuscript_id": identifier,
                "build_id": build_id,
                "kind": kind,
                "validation_build_id": validation["id"] if validation else None,
                "files": {p: snapshots[p] for p in paths},
                "inclusion_reasons": {
                    p: (manifest["reasons"].get(p) or ["Explicitly selected attachment"])
                    if kind == "submission"
                    else ["Internal archive selection"]
                    for p in paths
                },
                "dynamic_dependencies": manifest["unresolved"] if kind == "submission" else [],
                "dynamic_confirmed": bool(confirm_dynamic) if kind == "submission" else False,
                "archive_hash": digest,
                "created": time.time(),
            },
        )


def review_comment(store, command: ReviewInput, identifier=None, expected=None):
    with store.db(write=True) as db:
        submission = store.record(db, command.submission_id, "submission")
        if command.status == "addressed" and not command.response.strip():
            raise ValueError("An addressed review needs a response explaining the resolution.")
        if identifier:
            previous = store.record(db, identifier, "review")
            if previous["data"]["submission_id"] != command.submission_id:
                raise ValueError("A review remains attached to its original submission snapshot.")
        for change in command.changes:
            if not change.get("path") or not change.get("hash"):
                raise ValueError(
                    "A manuscript change needs a source path and reviewed content hash."
                )
            # Do not acquire the file lock inside a DB writer; callers capture
            # file versions before this transaction and validation is read-only.
            target = project_file(store, submission["data"]["manuscript_id"], change["path"])
            if sha(read_bounded(target, 2_000_000)) != change["hash"]:
                raise Conflict(
                    "A cited manuscript change has changed again. Review its current version."
                )
        for source in command.source_ids:
            store.record(db, source)
        row = store.put(
            db, "review", command.model_dump(), identifier=identifier, expected=expected
        )
        store.link(db, command.submission_id, row["id"], "reviews_snapshot")
        for source in command.source_ids:
            store.link(db, source, row["id"], "response_evidence")
        return row
