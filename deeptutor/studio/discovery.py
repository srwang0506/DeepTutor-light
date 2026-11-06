"""Immutable discovery pages, explicit outages, and resumable provider cursors."""

import asyncio
from email.utils import parsedate_to_datetime
import json
import os
import time

from defusedxml import ElementTree
import httpx

from .database import Conflict, Studio

PROVIDERS = {"openalex", "crossref", "arxiv"}


def parameters(provider, query, limit, cursor, order, year_from, year_to):
    if provider == "openalex":
        url, params = (
            "https://api.openalex.org/works",
            {"search": query, "per_page": limit, "cursor": cursor or "*"},
        )
        params["sort"] = "publication_date:desc" if order == "newest" else "relevance_score:desc"
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
    elif provider == "crossref":
        if not 0 <= int(cursor or 0) < 1000:
            raise ValueError(
                "Crossref sorted discovery covers the first 1,000 results. Narrow the query or date range."
            )
        url, params = (
            "https://api.crossref.org/works",
            {
                "query.bibliographic": query,
                "rows": min(limit, 1000 - int(cursor or 0)),
                "offset": int(cursor or 0),
                "sort": "published" if order == "newest" else "relevance",
                "order": "desc",
            },
        )
        filters = []
        if year_from:
            filters.append(f"from-pub-date:{year_from}-01-01")
        if year_to:
            filters.append(f"until-pub-date:{year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
    else:
        expression = (
            query
            if any(term in query for term in ("ti:", "au:", "abs:", "cat:", "all:", "id:"))
            else "all:" + query
        )
        if year_from or year_to:
            expression = f"({expression}) AND submittedDate:[{year_from or 1991}01010000 TO {year_to or 2200}12312359]"
        url, params = (
            "https://export.arxiv.org/api/query",
            {
                "search_query": expression,
                "start": int(cursor or 0),
                "max_results": limit,
                "sortBy": "submittedDate" if order == "newest" else "relevance",
                "sortOrder": "descending",
            },
        )
    return url, params


def retry_time(header):
    if not header:
        return time.time() + 30
    try:
        return time.time() + max(0, min(86400, int(header)))
    except ValueError:
        try:
            return parsedate_to_datetime(header).timestamp()
        except (ValueError, TypeError, OverflowError):
            return time.time() + 30


def overall(pages):
    last = {p["provider"]: p for p in pages}
    states = [p["status"] for p in last.values()]
    if all(s == "complete" for s in states):
        return "complete"
    if any(s in {"complete", "partial"} for s in states):
        return "partial"
    return "failed"


async def request_page(store, provider, options, cursor=None, seen=0, transport=None):
    url, params = parameters(
        provider,
        options["query"],
        options["limit"],
        cursor,
        options["order"],
        options.get("year_from"),
        options.get("year_to"),
    )
    page = {
        "provider": provider,
        "url": url,
        "query": params.copy(),
        "started": time.time(),
        "status": "running",
        "items": [],
        "cursor": cursor,
        "seen_before": seen,
    }
    actual = params.copy()
    # Optional host configuration, never persisted into snapshots or returned to clients.
    if provider == "openalex" and os.getenv("OPENALEX_API_KEY"):
        actual["api_key"] = os.environ["OPENALEX_API_KEY"]
    try:
        async with httpx.AsyncClient(
            timeout=25,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "DeepTutor-PersonalStudio/1.0"},
        ) as client:
            async with client.stream("GET", url, params=actual) as response:
                page["http_status"] = response.status_code
                page["retry_after"] = response.headers.get("retry-after")
                if response.status_code == 429:
                    page.update(status="rate_limited", retry_at=retry_time(page["retry_after"]))
                    return page
                if response.status_code in {401, 403}:
                    page["status"] = "authorization_required"
                    return page
                response.raise_for_status()
                content = bytearray()
                async for part in response.aiter_bytes():
                    content.extend(part)
                    if len(content) > 10_000_000:
                        raise ValueError("Response exceeds 10 MB.")
        page["raw_hash"] = store.save_blob(bytes(content))
        if provider == "arxiv":
            root = ElementTree.fromstring(content)
            ns = {
                "a": "http://www.w3.org/2005/Atom",
                "o": "http://a9.com/-/spec/opensearch/1.1/",
            }
            page["total"] = int(root.findtext("o:totalResults", "0", ns))
            for e in root.findall("a:entry", ns):
                identifier = e.findtext("a:id", "", ns)
                page["items"].append(
                    {
                        "id": identifier,
                        "title": " ".join(e.findtext("a:title", "", ns).split()),
                        "authors": [
                            a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)
                        ],
                        "year": int(e.findtext("a:published", "0000", ns)[:4]) or None,
                        "arxiv": identifier.rsplit("/abs/", 1)[-1],
                        "doi": "",
                        "url": identifier,
                        "references": [],
                    }
                )
        else:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Provider returned an unexpected response structure.")
            if provider == "openalex":
                if not isinstance(data.get("meta"), dict) or not isinstance(
                    data.get("results"), list
                ):
                    raise ValueError("OpenAlex response is missing metadata or results.")
                page["total"] = data.get("meta", {}).get("count")
                page["next_cursor"] = data.get("meta", {}).get("next_cursor")
                for e in data.get("results", []):
                    page["items"].append(
                        {
                            "id": e["id"],
                            "title": e.get("title") or "Untitled",
                            "authors": [
                                a["author"]["display_name"] for a in e.get("authorships", [])
                            ],
                            "year": e.get("publication_year"),
                            "doi": e.get("doi") or "",
                            "url": e.get("doi") or e["id"],
                            "references": e.get("referenced_works", []),
                        }
                    )
            else:
                payload = data.get("message")
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    raise ValueError("Crossref response is missing its result list.")
                page["total"] = payload.get("total-results")
                page["next_cursor"] = payload.get("next-cursor")
                for e in payload.get("items", []):
                    page["items"].append(
                        {
                            "id": e.get("DOI"),
                            "title": (e.get("title") or ["Untitled"])[0],
                            "authors": [
                                " ".join([a.get("given", ""), a.get("family", "")]).strip()
                                for a in e.get("author", [])
                            ],
                            "year": (e.get("published", {}).get("date-parts") or [[None]])[0][0],
                            "doi": e.get("DOI", ""),
                            "url": e.get("URL", ""),
                            "references": [
                                r["DOI"] for r in e.get("reference", []) if r.get("DOI")
                            ],
                        }
                    )

        count = len(page["items"])
        if provider in {"arxiv", "crossref"}:
            page["next_cursor"] = str(int(cursor or 0) + count)
        if provider == "crossref":
            page["coverage"] = {
                "mode": "bounded_sorted",
                "offset": int(cursor or 0),
                "returned": count,
                "maximum": 1000,
                "year_from": options.get("year_from"),
                "year_to": options.get("year_to"),
                "snapshot_stable": False,
            }
            if int(cursor or 0) + count >= 1000:
                page["next_cursor"] = None
                page["coverage"]["limited"] = (page.get("total") or 0) > 1000
        complete = count < options["limit"] or (
            page.get("total") is not None and seen + count >= page["total"]
        )
        if provider != "arxiv" and not page.get("next_cursor"):
            complete = True
        if not complete and cursor is not None and page.get("next_cursor") == cursor:
            raise ValueError("Provider repeated a pagination cursor; narrow or restart the query.")
        if complete:
            page["next_cursor"] = None
        page["status"] = (
            "complete" if complete and not page.get("coverage", {}).get("limited") else "partial"
        )
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        ElementTree.ParseError,
    ) as exc:
        page["status"] = "failed"
        page["error"] = type(exc).__name__
    finally:
        page["finished"] = time.time()
    return page


async def search(
    store: Studio,
    query,
    providers,
    limit=20,
    order="relevance",
    year_from=None,
    year_to=None,
    transport=None,
):
    if not query.strip() or len(query) > 2000:
        raise ValueError("Enter a search query up to 2,000 characters.")
    if not providers or not set(providers) <= PROVIDERS:
        raise ValueError("Choose supported discovery sources.")
    if order not in {"relevance", "newest"} or (year_from and year_to and year_from > year_to):
        raise ValueError("Choose a valid search order and year range.")
    options = {
        "query": query,
        "providers": list(dict.fromkeys(providers)),
        "limit": min(50, max(1, limit)),
        "order": order,
        "year_from": year_from,
        "year_to": year_to,
    }
    with store.db(write=True) as db:
        run = store.put(
            db, "discovery", {**options, "status": "running", "pages": [], "started": time.time()}
        )
    pages = await asyncio.gather(
        *(request_page(store, p, options, transport=transport) for p in options["providers"])
    )
    with store.db(write=True) as db:
        return store.put(
            db,
            "discovery",
            {**run["data"], "status": overall(pages), "pages": pages, "finished": time.time()},
            identifier=run["id"],
            expected=run["revision"],
        )


async def next_page(store, identifier, provider, expected, transport=None):
    if provider not in PROVIDERS:
        raise ValueError("Unsupported discovery provider.")
    with store.db(write=True) as db:
        run = store.record(db, identifier, "discovery")
        if run["revision"] != expected:
            raise Conflict("This search changed. Reload its latest pages.")
        data = run["data"]
        pending = data.get("inflight")
        if pending and pending["started"] > time.time() - 90:
            raise Conflict("A page is already being retrieved.")
        pages = [p for p in data["pages"] if p["provider"] == provider]
        if not pages:
            raise ValueError("Provider is not part of this search.")
        last = pages[-1]
        if last["status"] == "complete":
            raise ValueError("This provider has no further pages.")
        if last.get("retry_at", 0) > time.time():
            raise ValueError("This provider requested a retry delay. Try again later.")
        if provider == "arxiv" and last.get("started", 0) > time.time() - 3:
            raise ValueError("Wait at least three seconds between arXiv requests.")
        cursor = last.get("next_cursor") if last["status"] == "partial" else last.get("cursor")
        seen = sum(len(p["items"]) for p in pages if p["status"] in {"complete", "partial"})
        if len(data["pages"]) >= 60:
            raise ValueError("This snapshot has 60 pages. Start a new, narrower query.")
        run = store.put(
            db,
            "discovery",
            {**data, "inflight": {"provider": provider, "started": time.time()}},
            identifier=identifier,
            expected=expected,
        )
    options = {"limit": 20, "order": "relevance", **data}
    page = await request_page(store, provider, options, cursor, seen, transport)
    with store.db(write=True) as db:
        current = store.record(db, identifier, "discovery")
        pages = [*current["data"]["pages"], page]
        return store.put(
            db,
            "discovery",
            {
                **current["data"],
                "pages": pages,
                "inflight": None,
                "status": overall(pages),
                "finished": time.time(),
            },
            identifier=identifier,
            expected=current["revision"],
        )


def local_graph(store, discovery_id):
    run = store.get(discovery_id, "discovery")["data"]

    # DOI joins Crossref and OpenAlex records without relying on title similarity.
    def identity(item):
        from .catalog import normalize_doi

        try:
            return "doi:" + normalize_doi(item["doi"]) if item.get("doi") else item["id"]
        except ValueError:
            return item["id"]

    items, aliases = {}, {}
    for page in run["pages"]:
        for item in page["items"]:
            if item.get("id"):
                key = identity(item)
                aliases[item["id"]] = key
                if item.get("doi"):
                    aliases[item["doi"].removeprefix("https://doi.org/")] = key
                if key not in items:
                    items[key] = {**item, "id": key, "references": []}
                items[key]["references"].extend(item.get("references", []))
    edges = {
        (a, aliases[b])
        for a, item in items.items()
        for b in item["references"]
        if b in aliases and aliases[b] != a
    }
    return {
        "nodes": list(items.values()),
        "edges": [
            {"source": a, "target": b, "relation": "cites", "basis": "provider_reference_list"}
            for a, b in sorted(edges)
        ],
        "coverage": "Only reference edges returned in this snapshot are shown; missing edges remain unknown.",
    }


def collect(store, identifier, provider, item_id):
    from filelock import FileLock

    from . import catalog

    run = store.get(identifier, "discovery")
    item = next(
        (
            item
            for page in run["data"]["pages"]
            if page["provider"] == provider
            for item in page["items"]
            if item.get("id") == item_id
        ),
        None,
    )
    if not item:
        raise ValueError("Choose an item actually returned in this saved search.")
    source_key = provider + ":" + item_id
    with FileLock(store.root / ".collect.lock", timeout=10):
        with store.db() as db:
            known = db.execute(
                "SELECT edition_id FROM identifiers WHERE scheme='discovery' AND value=?",
                (source_key,),
            ).fetchone()
        if known:
            edition_id = known[0]
            catalog.link_project(store, edition_id, "reading")
        else:
            result = catalog.import_bibliography(
                store,
                json.dumps(
                    [
                        {
                            "id": "",
                            "title": item["title"],
                            "author": [{"literal": a} for a in item["authors"]],
                            "DOI": item.get("doi", ""),
                            "arxiv": item.get("arxiv", ""),
                            "URL": item.get("url", ""),
                            "issued": {"date-parts": [[item["year"]]]} if item.get("year") else {},
                        }
                    ]
                ),
                "csl",
            )["entries"][0]
            if result["status"] == "error":
                raise ValueError(result["detail"])
            edition_id = result["edition_id"]
        with store.db(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO identifiers VALUES ('discovery',?,?)",
                (source_key, edition_id),
            )
            origin = store.put(
                db,
                "collection",
                {
                    "discovery_id": identifier,
                    "discovery_revision": run["revision"],
                    "provider": provider,
                    "item_id": item_id,
                    "edition_id": edition_id,
                    "query": run["data"]["query"],
                    "collected": time.time(),
                },
            )
            store.link(db, identifier, origin["id"], "discovered_in")
            store.link(db, edition_id, origin["id"], "collected_edition")
        return {"edition_id": edition_id, "provenance": origin}
