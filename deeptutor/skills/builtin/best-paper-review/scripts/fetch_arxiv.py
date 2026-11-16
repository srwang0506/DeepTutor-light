"""Download a specific arXiv version and create a verified snapshot.

Usage:
    python3 fetch_arxiv.py 2501.13928 --version v1 --out WORK/sources [--id fast3r-v1] [--cutoff 2025-03-14]

Downloads https://arxiv.org/pdf/<id><ver>, records the version's public date
from the abs page submission history, runs extract_pdf, and prints the
`sources[]` entry with `date_verified` filled in. With --cutoff and no
--version, picks the latest version public on or before the cutoff.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from arxiv_meta import arxiv_versions  # noqa: E402
from common import http_get, now_iso  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arxiv_id")
    ap.add_argument("--version", default=None, help="e.g. v1")
    ap.add_argument("--cutoff", default=None, help="ISO date; choose latest version on/before it")
    ap.add_argument("--out", required=True)
    ap.add_argument("--id", default=None, help="source id (default: <arxiv_id>-<ver>)")
    ap.add_argument("--rel-to", default=None)
    ap.add_argument("--cache", default=os.environ.get("BPR_CACHE"), help="HTTP cache directory (default $BPR_CACHE)")
    a = ap.parse_args()

    meta = arxiv_versions(a.arxiv_id, cache_dir=a.cache)
    versions = meta["versions"]
    if not versions:
        print("could not read submission history; refusing to guess the version date", file=sys.stderr)
        return 2
    ver = a.version
    if ver is None:
        eligible = [v for v, d in versions.items() if a.cutoff is None or d <= a.cutoff]
        if not eligible:
            print(f"no version public on/before {a.cutoff}: {versions}", file=sys.stderr)
            return 3
        ver = max(eligible, key=lambda v: int(v[1:]))
    if ver not in versions:
        print(f"version {ver} not in history {versions}", file=sys.stderr)
        return 2
    date = versions[ver]
    if a.cutoff and date > a.cutoff:
        print(f"WARNING: {a.arxiv_id}{ver} public {date} is after cutoff {a.cutoff}", file=sys.stderr)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sid = a.id or f"{a.arxiv_id.replace('/', '_')}-{ver}"
    pdf = out / f"{sid}.pdf"
    if not pdf.exists():
        pdf.write_bytes(http_get(f"https://arxiv.org/pdf/{a.arxiv_id}{ver}", cache_dir=a.cache, timeout=180, max_bytes=150_000_000))
    extra = {
        "url": f"https://arxiv.org/abs/{a.arxiv_id}{ver}", "arxiv_id": a.arxiv_id, "version": ver,
        "public_date": date, "title": meta.get("title"), "all_versions": versions,
    }
    cmd = [sys.executable, str(HERE / "extract_pdf.py"), str(pdf), "--id", sid, "--role", "comparator",
           "--out", str(out), "--meta", json.dumps(extra, ensure_ascii=False)]
    if a.rel_to:
        cmd += ["--rel-to", a.rel_to]
    res = subprocess.run(cmd, capture_output=True, text=True)
    sys.stderr.write(res.stderr)
    if res.returncode:
        return res.returncode
    entry = json.loads(res.stdout)
    entry["date_verified"] = {"method": "arxiv-abs-history", "value": date, "url": meta["url"], "checked_at": now_iso()}
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
