"""Manuscript-owned citations and exact passage-to-evidence bindings."""

import re

from . import catalog, manuscripts
from .database import Conflict, sha
from .models import FileEdit


def citations(store, identifier, query=""):
    rows = []
    for file in manuscripts.file_list(store, identifier):
        if file["path"].endswith(".bib"):
            text = manuscripts.read_file(store, identifier, file["path"])["text"]
            for row in catalog.parse_bibliography(text, "bibtex"):
                rows.append({**row, "path": file["path"], "file_hash": file["hash"]})
    seen = [r["citekey"] for r in rows]
    return {
        "entries": [
            r
            for r in rows
            if query.casefold() in " ".join([r["citekey"], r["title"], *r["authors"]]).casefold()
        ],
        "duplicate_keys": sorted({k for k in seen if seen.count(k) > 1}),
    }


def add_citation(store, identifier, edition_id, key):
    if not re.fullmatch(r"[A-Za-z0-9_:.+-]{1,200}", key):
        raise ValueError("Use a valid manuscript citation key.")
    # The bibliography namespace is this manuscript, never the global library.
    with manuscripts.lock(store, identifier):
        existing = citations(store, identifier)["entries"]
        if any(r["citekey"] == key for r in existing):
            raise Conflict(
                "This citation key already exists in the manuscript. Select it or choose another key."
            )
        generated = catalog.bibliography(store, [edition_id], {edition_id: key})
        path = "studio-references.bib"
        target = manuscripts.project_file(store, identifier, path)
        current = (
            manuscripts.read_file(store, identifier, path)
            if target.exists()
            else {"text": "", "hash": sha(b"")}
        )
        result = manuscripts.save_file(
            store,
            identifier,
            FileEdit(path=path, base_hash=current["hash"], text=current["text"] + "\n" + generated),
        )
        with store.db(write=True) as db:
            row = store.put(
                db,
                "citation",
                {
                    "manuscript_id": identifier,
                    "edition_id": edition_id,
                    "citekey": key,
                    "path": path,
                    "file_hash": result["hash"],
                    "bibliography": generated,
                },
            )
            store.link(db, edition_id, row["id"], "bibliography_source")
            store.link(db, row["id"], identifier, "manuscript_bibliography")
        return {
            "citation": row,
            "file": result,
            "setup": r"Add studio-references to your existing bibliography command, or use \addbibresource{studio-references.bib} with biblatex. Existing macros are preserved.",
        }


def bind_passage(store, identifier, command):
    with manuscripts.lock(store, identifier):
        file = manuscripts.read_file(store, identifier, command.path)
        if file["hash"] != command.base_hash:
            raise Conflict(
                "The source file changed. Select the passage again before binding evidence."
            )
        if not 0 <= command.start < command.end <= len(file["text"]):
            raise ValueError("Select a nonempty passage within this file.")
        quote = file["text"][command.start : command.end]
        with store.db(write=True) as db:
            sources = [store.record(db, source) for source in command.source_ids]
            if not sources or any(
                r["kind"]
                not in {"evidence", "observation", "artifact", "claim", "run", "remote_snapshot"}
                for r in sources
            ):
                raise ValueError(
                    "Choose original evidence, observations, artifacts, claims, runs or source snapshots."
                )
            row = store.put(
                db,
                "passage",
                {
                    **command.model_dump(),
                    "manuscript_id": identifier,
                    "quote": quote,
                    "sources": [
                        {
                            "id": s["id"],
                            "kind": s["kind"],
                            "revision": s["revision"],
                            "data": s["data"],
                        }
                        for s in sources
                    ],
                    "verification": "user_linked_not_automatically_verified",
                },
            )
            for source in sources:
                store.link(db, source["id"], row["id"], "passage_evidence")
            store.link(db, row["id"], identifier, "manuscript_passage")
            return row


def passages(store, identifier):
    store.get(identifier, "manuscript")
    output = []
    for row in store.list("passage", limit=500):
        data = row["data"]
        if data["manuscript_id"] != identifier:
            continue
        try:
            file = manuscripts.read_file(store, identifier, data["path"])
            text = file["text"]
            if file["hash"] == data["base_hash"]:
                start, state = data["start"], "current"
            elif text.count(data["quote"]) == 1:
                start, state = text.index(data["quote"]), "exact_relocated"
            else:
                start, state = None, "ambiguous" if data["quote"] in text else "missing"
            line = text.count("\n", 0, start) + 1 if start is not None else None
        except (ValueError, OSError):
            line, state = None, "unavailable"
        output.append(
            {**row, "location": {"status": state, "line": line}, "stale": store.stale(row["id"])}
        )
    return output


def outline(store, identifier):
    result = []
    for file in manuscripts.file_list(store, identifier):
        if not file["path"].endswith(".tex"):
            continue
        text = manuscripts.read_file(store, identifier, file["path"])["text"]
        for match in re.finditer(
            r"\\(?P<kind>chapter|section|subsection|subsubsection|label)\*?(?:\[[^\]]*\])?\{(?P<title>[^}]+)\}",
            text,
        ):
            result.append(
                {
                    "path": file["path"],
                    "kind": match["kind"],
                    "title": match["title"],
                    "line": text.count("\n", 0, match.start()) + 1,
                }
            )
    return result
