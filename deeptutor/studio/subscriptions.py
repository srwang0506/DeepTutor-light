"""Opt-in saved searches and RSS/Atom feeds. Reading never implies inclusion."""

import asyncio
import logging
import time
from typing import Literal
from urllib.parse import urlsplit

from defusedxml import ElementTree
import httpx
from pydantic import Field, model_validator

from . import discovery
from .database import Conflict, Studio
from .models import Command


class SubscriptionInput(Command):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["search", "rss"] = "search"
    query: str = Field(default="", max_length=2000)
    providers: list[Literal["openalex", "crossref", "arxiv"]] = Field(
        default_factory=lambda: ["crossref"]
    )
    order: Literal["relevance", "newest"] = "relevance"
    year_from: int | None = Field(default=None, ge=1600, le=2200)
    year_to: int | None = Field(default=None, ge=1600, le=2200)
    url: str = Field(default="", max_length=4000)
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=6, le=720)

    @model_validator(mode="after")
    def valid_source(self):
        if self.kind == "search" and (not self.query.strip() or not self.providers):
            raise ValueError("A saved search needs a query and at least one source.")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("The start year must precede the end year.")
        if self.kind == "rss":
            url = urlsplit(self.url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.port not in {None, 443}
            ):
                raise ValueError(
                    "Use a public HTTPS RSS or Atom feed without embedded credentials."
                )
        return self


def save(store, command, identifier=None, expected=None):
    with store.db(write=True) as db:
        previous = store.record(db, identifier, "subscription")["data"] if identifier else {}
        if previous.get("inflight_at") and previous["inflight_at"] > time.time() - 90:
            if any(
                previous.get(key) != getattr(command, key)
                for key in (
                    "title",
                    "kind",
                    "query",
                    "providers",
                    "url",
                    "order",
                    "year_from",
                    "year_to",
                )
            ):
                raise Conflict("Wait for this refresh to finish before changing its source.")
        return store.put(
            db,
            "subscription",
            {
                **previous,
                **command.model_dump(),
                "next_check": time.time(),
                "inflight_at": previous.get("inflight_at"),
            },
            identifier=identifier,
            expected=expected,
        )


def parse_feed(content):
    root = ElementTree.fromstring(content)
    atom = "{http://www.w3.org/2005/Atom}"
    nodes = (
        root.findall(atom + "entry")
        if root.tag == atom + "feed"
        else root.findall("./channel/item")
    )
    if root.tag not in {atom + "feed", "rss"}:
        raise ValueError("This URL did not return an RSS or Atom feed.")
    items = []
    for e in nodes[:300]:
        prefix = atom if root.tag == atom + "feed" else ""
        title = (
            "".join(e.find(prefix + "title").itertext())
            if e.find(prefix + "title") is not None
            else "Untitled"
        )
        if prefix:
            url = next(
                (
                    link.get("href", "")
                    for link in e.findall(atom + "link")
                    if link.get("rel", "alternate") == "alternate"
                ),
                "",
            )
            identifier = e.findtext(atom + "id") or url
            authors = [a.findtext(atom + "name", "") for a in e.findall(atom + "author")]
        else:
            url = e.findtext("link", "")
            identifier = e.findtext("guid") or url
            authors = [e.findtext("author", "")]
        if not url.startswith(("https://", "http://")):
            url = ""
        if not identifier:
            continue
        items.append(
            {
                "id": identifier[:2000],
                "title": title[:1000],
                "url": url,
                "authors": authors,
                "year": None,
                "doi": "",
                "references": [],
            }
        )
    return items


async def read_feed(store, url, transport=None):
    from deeptutor.tools.web_fetch import _is_disallowed_host

    if transport is None and await asyncio.to_thread(_is_disallowed_host, urlsplit(url).hostname):
        raise ValueError("RSS sources must resolve to public Internet addresses.")
    page = {
        "provider": "rss",
        "url": url,
        "query": {},
        "started": time.time(),
        "items": [],
        "status": "running",
    }
    try:
        async with httpx.AsyncClient(
            timeout=25, follow_redirects=False, transport=transport
        ) as client:
            async with client.stream(
                "GET", url, headers={"User-Agent": "DeepTutor-PersonalStudio/1.0"}
            ) as response:
                page["http_status"] = response.status_code
                if response.status_code == 429:
                    page.update(
                        status="rate_limited", retry_after=response.headers.get("retry-after")
                    )
                    return page
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError(
                        "The feed moved. Update its URL before reading the new destination."
                    )
                content = bytearray()
                async for part in response.aiter_bytes():
                    content.extend(part)
                    if len(content) > 5_000_000:
                        raise ValueError("Feed exceeds 5 MB.")
        page["raw_hash"] = store.save_blob(bytes(content))
        page["items"] = parse_feed(content)
        page["status"] = "complete"
    except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
        page.update(status="failed", error=type(exc).__name__)
    finally:
        page["finished"] = time.time()
    return page


async def refresh(store, identifier, *, transport=None, only_due=False):
    with store.db(write=True) as db:
        row = store.record(db, identifier, "subscription")
        data = row["data"]
        if only_due and (not data.get("enabled") or data.get("next_check", 0) > time.time()):
            return row
        if data.get("inflight_at") and data["inflight_at"] > time.time() - 90:
            raise Conflict("This source is already refreshing.")
        row = store.put(
            db,
            "subscription",
            {**data, "inflight_at": time.time()},
            identifier=identifier,
            expected=row["revision"],
        )
    try:
        if data["kind"] == "search":
            run = await discovery.search(
                store,
                data["query"],
                data["providers"],
                30,
                order=data.get("order", "relevance"),
                year_from=data.get("year_from"),
                year_to=data.get("year_to"),
                transport=transport,
            )
        else:
            page = await read_feed(store, data["url"], transport)
            with store.db(write=True) as db:
                run = store.put(
                    db,
                    "discovery",
                    {
                        "query": data["title"],
                        "providers": ["rss"],
                        "status": discovery.overall([page]),
                        "pages": [page],
                        "started": page["started"],
                        "finished": time.time(),
                        "subscription_id": identifier,
                    },
                )
        current_ids = {item["id"] for page in run["data"]["pages"] for item in page["items"]}
        with store.db(write=True) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS subscription_seen (subscription TEXT NOT NULL REFERENCES records(id), item TEXT NOT NULL, seen REAL NOT NULL, PRIMARY KEY(subscription,item))"
            )
            for item_id in data.get("seen_ids", []):
                db.execute(
                    "INSERT OR IGNORE INTO subscription_seen VALUES (?,?,?)",
                    (identifier, item_id, time.time()),
                )
            new_ids = [
                item_id
                for item_id in sorted(current_ids)
                if not db.execute(
                    "SELECT 1 FROM subscription_seen WHERE subscription=? AND item=?",
                    (identifier, item_id),
                ).fetchone()
            ]
            for item_id in current_ids:
                db.execute(
                    "INSERT OR IGNORE INTO subscription_seen VALUES (?,?,?)",
                    (identifier, item_id, time.time()),
                )
            current = store.record(db, identifier, "subscription")
            return store.put(
                db,
                "subscription",
                {
                    **current["data"],
                    "inflight_at": None,
                    "next_check": time.time() + current["data"]["interval_hours"] * 3600,
                    "last_check": time.time(),
                    "last_status": run["data"]["status"],
                    "last_discovery_id": run["id"],
                    "new_ids": new_ids
                    if current_ids or run["data"]["status"] == "complete"
                    else current["data"].get("new_ids", []),
                },
                identifier=identifier,
                expected=current["revision"],
            )
    except Exception as exc:
        with store.db(write=True) as db:
            current = store.record(db, identifier, "subscription")
            store.put(
                db,
                "subscription",
                {
                    **current["data"],
                    "inflight_at": None,
                    "last_status": "failed",
                    "error": type(exc).__name__,
                    "next_check": time.time() + 3600,
                },
                identifier=identifier,
                expected=current["revision"],
            )
        raise


async def run_due(store):
    now = time.time()
    rows = [
        r
        for r in store.list("subscription", limit=500)
        if r["data"].get("enabled") and r["data"].get("next_check", 0) <= now
    ]
    for row in rows[:10]:
        try:
            await refresh(store, row["id"], only_due=True)
        except Conflict:
            continue  # Another worker has claimed it.
        except Exception:
            logging.getLogger(__name__).exception("Saved source refresh failed: %s", row["id"])


async def background():
    # One personal runtime runs one worker. HTTP requests claim each subscription
    # transactionally, so manual refreshes cannot duplicate an in-flight request.
    store = Studio()
    while True:
        await run_due(store)
        await asyncio.sleep(60)
