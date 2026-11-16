"""Read-only scholarly search adapters with provenance, paging, caching and dedup.

Search (saves raw responses + normalized records):
    python3 sources.py arxiv     "alternating attention multi-view" --out s/a.json [--pages 2] [--limit 25]
    python3 sources.py openalex  "feed-forward 3D reconstruction"   --out s/o.json
    python3 sources.py semantic  "pointmap regression"               --out s/s.json
    python3 sources.py dblp      "multi-view transformer reconstruction" --out s/d.json
    python3 sources.py crossref  "query words" | 10.1109/xxx (DOI)    --out s/c.json
    python3 sources.py openreview FORUM_ID [--discussion]           --out s/or.json
Citation chasing (Semantic Scholar graph):
    python3 sources.py semantic-refs  arXiv:2312.14132 --out s/refs.json
    python3 sources.py semantic-cites arXiv:2312.14132 --out s/cites.json
Merge and deduplicate candidate lists:
    python3 sources.py merge s/*.json --out candidates.json [--before 2025-03-14]

Environment: SEMANTIC_SCHOLAR_API_KEY, OPENALEX_API_KEY (optional), BPR_CACHE (cache dir).
A search response is a lead for discovery; never evidence of what a paper says.
Errors are recorded so that "provider unavailable" is not mistaken for "no prior work".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import http_get, now_iso, read_json, sha256_bytes, write_json  # noqa: E402

CACHE = os.environ.get("BPR_CACHE")


def _arxiv_id_from(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d{4}\.\d{4,5})", s)
    return m.group(1) if m else None


def _norm_title(t: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def _rec(provider, title=None, authors=None, date=None, year=None, arxiv_id=None, doi=None, venue=None, url=None, abstract=None):
    if date and not year:
        year = int(date[:4]) if date[:4].isdigit() else None
    return {"title": " ".join((title or "").split()) or None, "authors": authors or [], "date": date, "year": year,
            "arxiv_id": arxiv_id, "doi": (doi or "").replace("https://doi.org/", "") or None, "venue": venue,
            "url": url, "abstract": (abstract or "")[:1500] or None, "providers": [provider]}


# ---------------------------------------------------------------- providers
def _arxiv_query(q, before=None, relaxed=False):
    if ":" in q:  # already an arXiv query expression
        expr = q
    else:
        terms = re.findall(r'"[^"]+"|\S+', q)
        expr = (" OR " if relaxed else " AND ").join(f"all:{t}" for t in terms)
    if before:
        expr = f"({expr}) AND submittedDate:[199101010000 TO {before.replace('-', '')}2359]"
    return expr


def search_arxiv(q, page, limit, before=None, relaxed=False):
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": _arxiv_query(q, before, relaxed), "start": page * limit, "max_results": limit,
         "sortBy": "relevance"})
    raw = http_get(url, cache_dir=CACHE)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    recs = []
    for e in ET.fromstring(raw).findall("a:entry", ns):
        aid = _arxiv_id_from(e.findtext("a:id", "", ns))
        doi_el = e.find("{http://arxiv.org/schemas/atom}doi")
        recs.append(_rec("arxiv", e.findtext("a:title", "", ns), [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
                         (e.findtext("a:published", "", ns) or "")[:10] or None, None, aid,
                         doi_el.text if doi_el is not None else None, "arXiv", f"https://arxiv.org/abs/{aid}" if aid else None,
                         e.findtext("a:summary", "", ns)))
    return url, raw, recs


def search_openalex(q, page, limit, before=None):
    params = {"search": q, "per_page": limit, "page": page + 1}
    if before:
        params["filter"] = f"to_publication_date:{before}"
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    raw = http_get(url, cache_dir=CACHE)
    recs = []
    for w in json.loads(raw).get("results", []):
        ids = w.get("ids") or {}
        loc = (w.get("primary_location") or {})
        src = (loc.get("source") or {})
        aid = None
        for l in w.get("locations") or []:
            aid = aid or _arxiv_id_from((l or {}).get("landing_page_url"))
        recs.append(_rec("openalex", w.get("display_name"), [a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")],
                         w.get("publication_date"), w.get("publication_year"), aid, ids.get("doi"), src.get("display_name"),
                         loc.get("landing_page_url") or w.get("id")))
    return url.replace(os.environ.get("OPENALEX_API_KEY", "\0"), "***"), raw, recs


def _s2_headers():
    k = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    return {"x-api-key": k} if k else {}


S2_FIELDS = "title,authors,year,publicationDate,externalIds,venue,url,abstract"


def _s2_rec(p):
    ext = p.get("externalIds") or {}
    return _rec("semantic", p.get("title"), [a.get("name") for a in p.get("authors") or []], p.get("publicationDate"),
                p.get("year"), ext.get("ArXiv"), ext.get("DOI"), p.get("venue"), p.get("url"), p.get("abstract"))


def search_semantic(q, page, limit, before=None):
    params = {"query": q, "offset": page * limit, "limit": limit, "fields": S2_FIELDS}
    if before:
        params["publicationDateOrYear"] = f":{before}"
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    raw = http_get(url, headers=_s2_headers(), cache_dir=CACHE)
    return url, raw, [_s2_rec(p) for p in json.loads(raw).get("data", []) or []]


def _s2_graph(kind, pid, page, limit, before=None):
    key = "citedPaper" if kind == "references" else "citingPaper"
    url = f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(pid, safe=':')}/{kind}?" + urllib.parse.urlencode(
        {"offset": page * limit, "limit": limit, "fields": S2_FIELDS})
    raw = http_get(url, headers=_s2_headers(), cache_dir=CACHE)
    return url, raw, [_s2_rec(x[key]) for x in json.loads(raw).get("data", []) or [] if x.get(key)]


def search_dblp(q, page, limit, before=None):
    url = "https://dblp.org/search/publ/api?" + urllib.parse.urlencode({"q": q, "format": "json", "h": limit, "f": page * limit})
    raw = http_get(url, cache_dir=CACHE)
    hits = (((json.loads(raw).get("result") or {}).get("hits") or {}).get("hit")) or []
    recs = []
    for h in hits:
        i = h.get("info", {})
        au = (i.get("authors") or {}).get("author") or []
        au = au if isinstance(au, list) else [au]
        ee = i.get("ee") if isinstance(i.get("ee"), str) else None
        recs.append(_rec("dblp", i.get("title"), [a.get("text") if isinstance(a, dict) else a for a in au], None,
                         int(i["year"]) if str(i.get("year", "")).isdigit() else None, _arxiv_id_from(ee), i.get("doi"),
                         i.get("venue") if isinstance(i.get("venue"), str) else None, ee or i.get("url")))
    return url, raw, recs


def search_crossref(q, page, limit, before=None):
    if re.match(r"^10\.\d{4,9}/", q):
        url = "https://api.crossref.org/works/" + urllib.parse.quote(q, safe="")
        raw = http_get(url, cache_dir=CACHE)
        items = [json.loads(raw)["message"]]
    else:
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"query.bibliographic": q, "rows": limit, "offset": page * limit})
        raw = http_get(url, cache_dir=CACHE)
        items = json.loads(raw)["message"].get("items", [])
    recs = []
    for it in items:
        dp = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        date = "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(dp)) if dp and dp[0] else None
        recs.append(_rec("crossref", (it.get("title") or [None])[0], [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in it.get("author", [])],
                         date if date and len(date) == 10 else None, dp[0] if dp else None, None, it.get("DOI"),
                         (it.get("container-title") or [None])[0], it.get("URL")))
    return url, raw, recs


def search_openreview(q, page, limit, before=None, discussion=False):
    url = "https://api2.openreview.net/notes?" + urllib.parse.urlencode({("forum" if discussion else "id"): q, "limit": 1000})
    raw = http_get(url, cache_dir=CACHE)
    recs = []
    for n in json.loads(raw).get("notes", []):
        c = n.get("content") or {}
        val = lambda k: (c.get(k) or {}).get("value") if isinstance(c.get(k), dict) else c.get(k)  # noqa: E731
        recs.append(_rec("openreview", val("title"), val("authors") or [], None, None, None, None, n.get("invitation") or (n.get("invitations") or [None])[0],
                         f"https://openreview.net/forum?id={n.get('forum')}", val("abstract")))
    return url, raw, recs


PROVIDERS = {"arxiv": search_arxiv, "openalex": search_openalex, "semantic": search_semantic, "dblp": search_dblp,
             "crossref": search_crossref, "openreview": search_openreview,
             "semantic-refs": lambda q, p, l, b=None: _s2_graph("references", q, p, l),
             "semantic-cites": lambda q, p, l, b=None: _s2_graph("citations", q, p, l)}


def run_search(provider, query, pages=1, limit=20, discussion=False, before=None):
    result = {"provider": provider, "query": query, "before": before, "retrieved_at": now_iso(), "requests": [], "records": [],
              "scope": f"{pages} page(s) x {limit}; bounded, not exhaustive", "discussion_included": discussion}
    fn = PROVIDERS[provider]
    for page in range(pages):
        try:
            if provider == "openreview":
                url, raw, recs = fn(query, page, limit, None, discussion)
            else:
                url, raw, recs = fn(query, page, limit, before)
            if not raw.strip():
                raise ValueError("empty response (provider busy, e.g. HTTP 202)")
            if provider == "arxiv" and page == 0 and not recs and ":" not in query and len(query.split()) > 1:
                # AND over many words is often too strict: retry once with OR between terms
                url, raw, recs = search_arxiv(query, page, limit, before, relaxed=True)
                result["relaxed_query"] = True
        except Exception as e:  # record failure explicitly
            result["requests"].append({"page": page, "error": f"{type(e).__name__}: {e}"})
            result["coverage_warning"] = "provider error: coverage unknown (not evidence of absence)"
            break
        result["requests"].append({"page": page, "url": url, "sha256": sha256_bytes(raw), "n": len(recs)})
        result.setdefault("raw", []).append(raw.decode("utf-8", errors="replace"))
        result["records"] += recs
        if len(recs) < limit or provider == "openreview":
            break
    return result


# ---------------------------------------------------------------- merge
def merge(files, before=None):
    merged, order = {}, []
    alias = {}
    for f in files:
        d = read_json(f)
        for r in d.get("records", []):
            keys = [k for k in (f"arxiv:{r['arxiv_id']}" if r.get("arxiv_id") else None,
                                f"doi:{r['doi'].lower()}" if r.get("doi") else None,
                                f"title:{_norm_title(r.get('title'))}" if _norm_title(r.get("title")) else None) if k]
            if not keys:
                continue
            hit = next((alias[k] for k in keys if k in alias), None)
            if hit is None:
                hit = keys[0]
                merged[hit] = dict(r, providers=list(r["providers"]), queries=[d.get("query")])
                order.append(hit)
            else:
                m = merged[hit]
                for fld in ("arxiv_id", "doi", "date", "year", "venue", "abstract", "url"):
                    if not m.get(fld) and r.get(fld):
                        m[fld] = r[fld]
                if m.get("date") and r.get("date") and r["date"] < m["date"]:
                    m["date"] = r["date"]  # earliest known date across providers
                m["providers"] = sorted(set(m["providers"]) | set(r["providers"]))
                if d.get("query") not in m["queries"]:
                    m["queries"].append(d.get("query"))
            for k in keys:
                alias[k] = hit
    out = [merged[k] for k in order]
    for r in out:
        r["hits"] = len(r["providers"]) + len(r["queries"]) - 1
        if before:
            d = r.get("date") or (f"{r['year']}-12-31" if r.get("year") else None)
            r["before_cutoff"] = None if d is None else (d <= before)
    out.sort(key=lambda r: -r["hits"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("provider", choices=list(PROVIDERS) + ["merge"])
    ap.add_argument("query", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--discussion", action="store_true", help="OpenReview: include forum replies (only after freezing your review)")
    ap.add_argument("--before", default=None, help="search: restrict to items public on/before this date where the provider supports it; merge: flag records")
    a = ap.parse_args()
    if a.provider == "merge":
        recs = merge(a.query, a.before)
        write_json(a.out, {"merged_from": a.query, "created_at": now_iso(), "cutoff": a.before, "records": recs})
        print(f"{len(recs)} unique records -> {a.out}")
        return 0
    res = run_search(a.provider, " ".join(a.query), a.pages, a.limit, a.discussion, a.before)
    if Path(a.out).exists():
        print(f"refusing to overwrite {a.out}", file=sys.stderr)
        return 2
    write_json(a.out, res)
    errs = [r for r in res["requests"] if "error" in r]
    print(f"{len(res['records'])} records -> {a.out}" + (f" | ERROR: {errs[0]['error']}" if errs else ""))
    return 1 if errs and not res["records"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
