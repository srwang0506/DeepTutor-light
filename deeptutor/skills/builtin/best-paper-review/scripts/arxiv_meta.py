"""arXiv / Crossref metadata helpers shared by fetch_arxiv.py and verify_dates.py."""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import http_get  # noqa: E402

ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/)?(?P<id>\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?P<ver>v\d+)?", re.I)
_HISTORY_RE = re.compile(r"\[(v\d+)\](?:\s*</a>)?\s*(?:</strong>)?\s*\w{3},\s*(\d{1,2})\s+(\w{3})\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def parse_arxiv_id(text: str) -> tuple[str, str | None] | None:
    m = ARXIV_ID_RE.search(text or "")
    if not m:
        return None
    return m.group("id"), (m.group("ver") or None)


def parse_history(html: str) -> dict[str, str]:
    """Return {'v1': 'YYYY-MM-DD', ...} from an arXiv abs page's submission history."""
    out: dict[str, str] = {}
    for ver, day, mon, year in _HISTORY_RE.findall(html):
        if mon[:3] in _MONTHS and ver not in out:
            out[ver] = dt.date(int(year), _MONTHS[mon[:3]], int(day)).isoformat()
    return out


def arxiv_versions(arxiv_id: str, cache_dir=None) -> dict:
    url = f"https://arxiv.org/abs/{arxiv_id}"
    html = http_get(url, cache_dir=cache_dir).decode("utf-8", errors="replace")
    title_m = re.search(r'<meta name="citation_title" content="([^"]+)"', html)
    return {"url": url, "title": title_m.group(1) if title_m else None, "versions": parse_history(html)}


def arxiv_api_entry(arxiv_id: str, cache_dir=None) -> dict:
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id})
    root = ET.fromstring(http_get(url, cache_dir=cache_dir))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    e = root.find("a:entry", ns)
    if e is None:
        return {}
    return {
        "title": " ".join((e.findtext("a:title", "", ns) or "").split()),
        "published": (e.findtext("a:published", "", ns) or "")[:10],
        "updated": (e.findtext("a:updated", "", ns) or "")[:10],
        "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
    }


def crossref_date(doi: str, cache_dir=None) -> dict:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    msg = json.loads(http_get(url, cache_dir=cache_dir))["message"]
    best = None
    for key in ("published-online", "published-print", "issued", "created"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            p = parts[0] + [1, 1]
            d = dt.date(p[0], p[1] or 1, p[2] or 1).isoformat()
            best = d if best is None or d < best else best
    return {"url": url, "date": best, "title": (msg.get("title") or [None])[0]}
