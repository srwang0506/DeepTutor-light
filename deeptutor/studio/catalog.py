"""Bibliographic identity and immutable originals, independent of project use."""

import json
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit

from .database import Conflict, Studio
from .models import EditionInput, WorkInput


def normalize_doi(value):
    value = unquote(value.strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    if value and not re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I):
        raise ValueError("Enter a valid DOI.")
    return value.casefold()


def normalize_arxiv(value):
    value = re.sub(
        r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)", "", value.strip(), flags=re.I
    )
    value = re.sub(r"\.pdf$", "", value)
    if value and not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v[1-9]\d*)?", value, re.I):
        raise ValueError("Enter a valid arXiv identifier, including its version when known.")
    return value.casefold()


def create_work(store: Studio, command: WorkInput):
    with store.db(write=True) as db:
        # Similar titles are intentionally not merged. Identity needs an exact identifier.
        return store.put(db, "work", command.model_dump())


def create_edition(store: Studio, command: EditionInput):
    data = command.model_dump()
    data["doi"] = normalize_doi(data["doi"])
    data["arxiv"] = normalize_arxiv(data["arxiv"])
    if data["url"] and urlsplit(data["url"]).scheme not in {"http", "https"}:
        raise ValueError("Source URLs must use HTTP or HTTPS.")
    with store.db(write=True) as db:
        return _create_edition(store, db, data)


def _create_edition(store, db, data):
    store.record(db, data["work_id"], "work")
    for scheme in ("doi", "arxiv"):
        if data[scheme]:
            existing = db.execute(
                "SELECT edition_id FROM identifiers WHERE scheme=? AND value=?",
                (scheme, data[scheme]),
            ).fetchone()
            if existing:
                raise Conflict(
                    f"This {scheme} already belongs to edition {existing[0]}. Review that record instead of merging titles."
                )
    row = store.put(db, "edition", {**data, "files": [], "full_text": "missing"})
    for scheme in ("doi", "arxiv"):
        if data[scheme]:
            db.execute("INSERT INTO identifiers VALUES (?,?,?)", (scheme, data[scheme], row["id"]))
    store.link(db, data["work_id"], row["id"], "edition_of")
    return row


def attach_pdf(store: Studio, edition_id, data: bytes, name, expected):
    import fitz

    if not data.startswith(b"%PDF-"):
        raise ValueError("Choose a valid PDF original.")
    with fitz.open(stream=data, filetype="pdf") as pdf:
        if pdf.needs_pass or not 0 < len(pdf) <= 10000:
            raise ValueError("The PDF is encrypted, empty, or exceeds 10,000 pages.")
        pages = len(pdf)
        outline = [{"level": r[0], "title": r[1], "page": r[2]} for r in pdf.get_toc()[:1000]]
    digest = store.save_blob(data)
    with store.db(write=True) as db:
        edition = store.record(db, edition_id, "edition")
        if edition["revision"] != expected:
            raise Conflict("The edition changed while uploading. Reload before attaching.")
        if any(f["hash"] == digest for f in edition["data"]["files"]):
            return edition
        payload = {**edition["data"], "full_text": "available"}
        payload["files"] = [
            *payload["files"],
            {
                "hash": digest,
                "name": Path(name).name,
                "pages": pages,
                "outline": outline,
                "size": len(data),
                "mime": "application/pdf",
            },
        ]
        return store.put(db, "edition", payload, identifier=edition_id, expected=expected)


def edition_file(store, edition_id, digest):
    row = store.get(edition_id, "edition")
    file = next((f for f in row["data"]["files"] if f["hash"] == digest), None)
    if not file:
        raise ValueError("This original does not belong to the selected edition.")
    return store.blob_path(digest), file


def page_source(store, edition_id, digest, page):
    import fitz

    path, metadata = edition_file(store, edition_id, digest)
    if not 1 <= page <= metadata["pages"]:
        raise ValueError("Page is outside this original.")
    with fitz.open(path) as pdf:
        p = pdf[page - 1]
        return {
            "text": p.get_text()[:100000],
            "page": page,
            "label": p.get_label(),
            "width": p.rect.width,
            "height": p.rect.height,
            "parser_signature": f"pymupdf-{fitz.VersionBind}",
            "blob_hash": digest,
        }


def link_project(store, edition_id, role="reading"):
    if role not in {"reading", "background", "method", "baseline", "excluded"}:
        raise ValueError("Unknown project use.")
    with store.db(write=True) as db:
        store.record(db, edition_id, "edition")
        db.execute(
            "INSERT INTO project_library VALUES (?,?,?,?) ON CONFLICT(project,edition) DO UPDATE SET role=excluded.role",
            (store.project, edition_id, role, time.time()),
        )
    return {"edition_id": edition_id, "project_id": store.project, "role": role}


def library(store, query="", project_only=False):
    works = store.list("work", query, 500)
    editions = store.list("edition", limit=500)
    with store.db() as db:
        links = {
            r["edition"]: r["role"]
            for r in db.execute("SELECT * FROM project_library WHERE project=?", (store.project,))
        }
    return [
        {
            **work,
            "editions": [
                {**e, "project_role": links.get(e["id"])}
                for e in editions
                if e["data"]["work_id"] == work["id"] and (not project_only or e["id"] in links)
            ],
        }
        for work in works
        if not project_only
        or any(e["data"]["work_id"] == work["id"] and e["id"] in links for e in editions)
    ]


def parse_bibliography(text, format):
    if len(text.encode()) > 5_000_000:
        raise ValueError("Bibliography exceeds 5 MB.")
    if format == "bibtex":
        import bibtexparser
        from bibtexparser.bparser import BibTexParser

        parser = BibTexParser(common_strings=True)
        parser.ignore_nonstandard_types = False
        rows = bibtexparser.loads(text, parser).entries
        return [
            {
                "title": r.get("title", ""),
                "authors": r.get("author", "").split(" and "),
                "year": r.get("year"),
                "doi": r.get("doi", ""),
                "arxiv": r.get("eprint", "")
                if r.get("archiveprefix", "").casefold() == "arxiv"
                else "",
                "citekey": r.get("ID", ""),
                "venue": r.get("journal", r.get("booktitle", "")),
                "url": r.get("url", ""),
                "original": r,
            }
            for r in rows
        ]
    if format == "csl":
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = [rows]
        return [
            {
                "title": r.get("title", ""),
                "authors": [
                    a.get("literal") or " ".join([a.get("given", ""), a.get("family", "")]).strip()
                    for a in r.get("author", [])
                ],
                "year": ((r.get("issued") or {}).get("date-parts") or [[None]])[0][0],
                "doi": r.get("DOI", ""),
                "arxiv": r.get("arxiv", ""),
                "citekey": str(r.get("id", "")),
                "url": r.get("URL", ""),
                "original": r,
            }
            for r in rows
        ]
    if format == "ris":
        rows, current = [], {}
        last = ""
        for line in text.splitlines():
            match = re.match(r"^([A-Z0-9]{2})  - ?(.*)$", line)
            if match:
                last, value = match.groups()
                if last == "TY":
                    current = {}
                elif last == "ER":
                    rows.append(current)
                    current = {}
                else:
                    current.setdefault(last, []).append(value)
            elif last and line.strip() and current.get(last):
                current[last][-1] += " " + line.strip()
        return [
            {
                "title": (r.get("TI") or r.get("T1") or [""])[0],
                "authors": r.get("AU", r.get("A1", [])),
                "year": (r.get("PY") or r.get("Y1") or [""])[0][:4],
                "doi": r.get("DO", [""])[0],
                "url": r.get("UR", [""])[0],
                "venue": r.get("JO", [""])[0],
                "original": r,
            }
            for r in rows
        ]
    raise ValueError("Supported formats: bibtex, ris, csl.")


def import_bibliography(store, text, format):
    parsed = parse_bibliography(text, format)
    if not parsed or len(parsed) > 1000:
        raise ValueError("Import between 1 and 1,000 entries at a time.")
    results = []
    for index, item in enumerate(parsed):
        try:
            doi = normalize_doi(item.get("doi", ""))
            arxiv = normalize_arxiv(item.get("arxiv", ""))
            year = item.get("year")
            command = WorkInput(
                title=item["title"],
                authors=[a for a in item.get("authors", []) if a],
                year=int(year) if year else None,
            )
            edition_command = EditionInput(
                work_id="",
                doi=doi,
                arxiv=arxiv,
                citekey=item.get("citekey", ""),
                venue=item.get("venue", ""),
                url=item.get("url", ""),
            )
            if edition_command.url and urlsplit(edition_command.url).scheme not in {
                "http",
                "https",
            }:
                raise ValueError("Source URLs must use HTTP or HTTPS.")
            with store.db(write=True) as db:
                known = db.execute(
                    "SELECT edition_id FROM identifiers WHERE (scheme='doi' AND value=? AND value!='') OR (scheme='arxiv' AND value=? AND value!='')",
                    (doi, arxiv),
                ).fetchall()
                identities = {r[0] for r in known}
                if len(identities) > 1:
                    raise Conflict("Identifiers refer to different editions. Resolve manually.")
                if identities:
                    edition = store.record(db, next(iter(identities)), "edition")
                    db.execute(
                        "INSERT OR IGNORE INTO project_library VALUES (?,?,?,?)",
                        (store.project, edition["id"], "reading", time.time()),
                    )
                    results.append(
                        {"index": index, "status": "existing", "edition_id": edition["id"]}
                    )
                    continue
                work = store.put(db, "work", command.model_dump())
                edition = _create_edition(
                    store, db, {**edition_command.model_dump(), "work_id": work["id"]}
                )
                db.execute(
                    "INSERT INTO project_library VALUES (?,?,?,?)",
                    (store.project, edition["id"], "reading", time.time()),
                )
            results.append({"index": index, "status": "imported", "edition_id": edition["id"]})
        except (ValueError, KeyError, TypeError) as exc:
            results.append({"index": index, "status": "error", "detail": str(exc)})
    return {"entries": results, "total": len(parsed)}


def bibliography(store, edition_ids, keys=None):
    import bibtexparser
    from bibtexparser.bibdatabase import BibDatabase

    entries = []
    seen = set()
    for identifier in edition_ids:
        edition = store.get(identifier, "edition")["data"]
        work = store.get(edition["work_id"], "work")["data"]
        key = (keys or {}).get(identifier) or edition["citekey"] or "ref" + identifier[:10]
        if not re.fullmatch(r"[A-Za-z0-9_:.+-]+", key) or key in seen:
            raise Conflict("Citation keys must be valid and unique within this bibliography.")
        seen.add(key)
        entry = {
            "ENTRYTYPE": "article",
            "ID": key,
            "title": work["title"],
            "author": " and ".join(work["authors"]),
        }
        for name, value in {
            "year": work["year"],
            "doi": edition["doi"],
            "url": edition["url"],
            "journal": edition["venue"],
        }.items():
            if value:
                entry[name] = str(value)
        entries.append(entry)
    database = BibDatabase()
    database.entries = entries
    return bibtexparser.dumps(database)
