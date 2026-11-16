"""Verify declared public dates/versions of sources against arXiv or Crossref.

Usage:
    python3 verify_dates.py WORK/review.json [--write] [--cache DIR]

For each source with an arXiv id (field `arxiv_id` or an arxiv.org URL) the
version's date is read from the arXiv submission history; for a `doi` the
earliest Crossref publication date is used. Mismatches with `public_date`
are reported. With --write, `date_verified` is stored in review.json (and
`public_date` filled when it was null). Exit code 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arxiv_meta import arxiv_versions, crossref_date, parse_arxiv_id  # noqa: E402
from common import now_iso, read_json, write_json  # noqa: E402


def verify_source(src: dict, cache=None) -> dict:
    """Return {'status': ok|mismatch|unverifiable|error, 'value', 'method', 'url', 'message'}."""
    aid = src.get("arxiv_id")
    ver = src.get("version") if isinstance(src.get("version"), str) and src.get("version", "").startswith("v") else None
    if not aid:
        parsed = parse_arxiv_id(src.get("url", "") or "")
        if parsed and "arxiv" in (src.get("url") or ""):
            aid, ver = parsed[0], ver or parsed[1]
    try:
        if aid:
            meta = arxiv_versions(aid, cache_dir=cache)
            ver = ver or "v1"
            value = meta["versions"].get(ver)
            if value is None:
                return {"status": "unverifiable", "message": f"{aid}{ver} not in history {meta['versions']}"}
            res = {"method": "arxiv-abs-history", "value": value, "url": meta["url"]}
        elif src.get("doi"):
            cr = crossref_date(src["doi"], cache_dir=cache)
            if not cr["date"]:
                return {"status": "unverifiable", "message": "Crossref has no date"}
            res = {"method": "crossref-earliest", "value": cr["date"], "url": cr["url"]}
        else:
            return {"status": "unverifiable", "message": "no arxiv_id/doi"}
    except Exception as e:  # network or parse errors: coverage unknown, not "wrong"
        return {"status": "error", "message": f"{type(e).__name__}: {e}"}
    declared = src.get("public_date")
    res["status"] = "ok" if declared in (None, res["value"]) else "mismatch"
    if res["status"] == "mismatch":
        res["message"] = f"declared {declared} but {res['method']} says {res['value']}"
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--roles", default="comparator,other", help="roles to verify (comma separated)")
    a = ap.parse_args()
    data = read_json(a.review)
    roles = set(a.roles.split(","))
    bad = 0
    for s in data.get("sources", []):
        if s.get("role", "comparator") not in roles:
            continue
        r = verify_source(s, cache=a.cache)
        print(f"{s.get('id')}: {r['status']} {r.get('value', '')} {r.get('message', '')}")
        if r["status"] == "mismatch":
            bad += 1
        if a.write and r["status"] in ("ok", "mismatch"):
            s["date_verified"] = {"method": r["method"], "value": r["value"], "url": r["url"], "checked_at": now_iso()}
            if s.get("public_date") is None:
                s["public_date"] = r["value"]
    if a.write:
        write_json(a.review, data)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
