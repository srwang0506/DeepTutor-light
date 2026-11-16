"""Create the review.json skeleton (step 1) so later steps only add to it.

    python3 init_review.py paper.pdf --work WORK --mode full --venue ICML --year 2026 \
        --cutoff 2025-03-14 --version "arXiv v1" [--arxiv 2503.11651] [--type method]

Copies nothing: paper.pdf is referenced relative to WORK. Runs extract_pdf.py
for the manuscript, fills paper hash/pages, the nine dimensions (empty), and
the manuscript source entry. Refuses to overwrite an existing review.json.
Edit review.json in place afterwards (or with a script that loads and saves
it) - never regenerate it from scratch, or merged novelty/sources are lost.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import integrity  # noqa: E402
from common import DIMENSIONS, read_json, sha256_file, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--work", required=True)
    ap.add_argument("--mode", default="full", choices=["quick", "full", "self", "rebuttal"])
    ap.add_argument("--venue", default=None)
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--track", default="main")
    ap.add_argument("--cutoff", required=True)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--title", default=None)
    ap.add_argument("--arxiv", default=None, help="arXiv id of the manuscript, if public")
    ap.add_argument("--type", default="method", help="paper_type (see references/paper-types.md)")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    rv = work / "review.json"
    if rv.exists():
        print(f"{rv} already exists; edit it in place", file=sys.stderr)
        return 2
    pdf = Path(a.pdf).resolve()
    if a.venue:
        vj = read_json(HERE.parent / "assets" / "venues.json")
        if a.venue not in vj["venues"]:
            print(f"unknown venue {a.venue}; see references/venues.md", file=sys.stderr)
            return 2
    meta = {"version": a.version}
    if a.arxiv:
        meta.update(arxiv_id=a.arxiv, url=f"https://arxiv.org/abs/{a.arxiv}{a.version if a.version.startswith('v') else ''}")
    res = subprocess.run([sys.executable, str(HERE / "extract_pdf.py"), str(pdf), "--id", "paper", "--role", "manuscript",
                          "--out", str(work / "sources"), "--rel-to", str(work), "--meta", json.dumps(meta)],
                         capture_output=True, text=True)
    sys.stderr.write(res.stderr)
    if res.returncode:
        return res.returncode
    src = json.loads(res.stdout)
    man = read_json(work / src["manifest"])
    title = a.title or src.get("title") or pdf.stem
    review = {
        "schema_version": 2, "language": "zh-CN", "mode": a.mode,
        "paper": {"title": title, "version": a.version, "pdf": os.path.relpath(pdf, work.resolve()),
                  "pdf_sha256": sha256_file(pdf), "pages": man["page_count"], "paper_type": a.type, "domain": ""},
        "venue": {"id": a.venue, "year": a.year, "track": a.track} if a.venue else None,
        "cutoff": a.cutoff, "review_date": dt.date.today().isoformat(),
        "policy": {"blind": True, "read_scope": "", "not_done": [], "searches": "", "withdrawn": [],
                   "prompt_injection": [f"p.{h['page']}: {h['match']}" for h in man.get("injection_hits", [])][:5],
                   "user_stance": {"kind": [], "summary": "", "handling": ""},
                   "neutrality": {"symmetry_check": "", "briefs_checked": False}},
        "provenance": integrity.provenance(),
        "summary": {"one_line": "", "problem": "", "method": "", "setup": "", "contributions": []},
        "overall": None,
        "dimensions": {k: {"status": "unchecked", "comment_ids": [], "note": ""} for k in DIMENSIONS},
        "sources": [src], "comments": [], "numeric_checks": [], "novelty": [],
    }
    write_json(rv, review)
    print(f"写出 {rv}（{man['page_count']} 页）。之后在原文件上增量编辑，不要整体重写。")
    print("记录使用者表态：policy.user_stance.kind（没有表态写 [\"none\"]），见 references/neutrality.md。")
    if not review["provenance"]["rules_official"]:
        print("注意：中立性等规则文件与官方清单不一致，本次审稿会被标记为“规则已修改”。")
    if man.get("injection_hits"):
        print(f"注意：发现 {len(man['injection_hits'])} 处疑似提示注入文字，已记入 policy.prompt_injection。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
