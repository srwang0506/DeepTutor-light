"""Locate a quote in a snapshot and print the page(s) it is on.

    python3 find_quote.py WORK/sources/paper.txt "exact words from the paper"
    python3 find_quote.py WORK/sources/paper.txt --grep "keyword"     # search, print page + context
    python3 find_quote.py - "quote" --rect --pdf paper.pdf --page 3      # region rect [x,y,w,h] for a quote

Use it to get the `page` of every evidence quote and to copy quotes verbatim.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import normalize_text, read_json  # noqa: E402
from evidence import Snapshot  # noqa: E402


def quote_rect(pdf: Path, page: int, quote: str, pad: float = 0.004):
    """Union bbox (normalized to the page) of the words matching `quote` on `page` (poppler pdftotext -bbox-layout)."""
    exe = shutil.which("pdftotext")
    if not exe:
        raise SystemExit("pdftotext (poppler) is required for --rect")
    out = subprocess.run([exe, "-bbox-layout", "-f", str(page), "-l", str(page), str(pdf), "-"], capture_output=True, text=True).stdout
    m = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', out)
    if not m:
        return None
    W, H = float(m.group(1)), float(m.group(2))
    words = [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in
             re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', out)]
    key = lambda s: "".join(ch.lower() for ch in normalize_text(s) if not ch.isspace() and ch != "-")  # noqa: E731
    target = key(quote)
    if not target:
        return None
    stream, owner = "", []
    for i, w in enumerate(words):
        k = key(w[4])
        stream += k
        owner += [i] * len(k)
    at = stream.find(target)
    if at < 0:
        return None
    idx = sorted(set(owner[at:at + len(target)]))
    x0 = min(words[i][0] for i in idx) / W - pad
    y0 = min(words[i][1] for i in idx) / H - pad
    x1 = max(words[i][2] for i in idx) / W + pad
    y1 = max(words[i][3] for i in idx) / H + pad
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(1, x1), min(1, y1)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot")
    ap.add_argument("quote", nargs="?")
    ap.add_argument("--grep", default=None, help="case-insensitive regex; prints matches with context")
    ap.add_argument("--context", type=int, default=160)
    ap.add_argument("--rect", action="store_true", help="print normalized [x,y,w,h] of the quote on --page of --pdf (for region comments)")
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--page", type=int, default=None)
    ap.add_argument("--pad", type=float, default=0.004)
    a = ap.parse_args()
    if a.rect:
        r = quote_rect(Path(a.pdf), a.page, a.quote or "", a.pad)
        print(json.dumps(r) if r else "not found on that page (try a shorter, single-paragraph quote)")
        return 0 if r else 1
    txt = Path(a.snapshot)
    man = Path(str(txt)[:-4] + ".manifest.json")
    pages = read_json(man)["pages"] if man.exists() else None
    snap = Snapshot(txt.read_text(encoding="utf-8"), pages)
    if a.grep:
        for m in re.finditer(a.grep, snap.text, re.IGNORECASE):
            s, e = max(0, m.start() - a.context), min(len(snap.text), m.end() + a.context)
            print(f"p.{snap.page_of(m.start())}: ...{snap.text[s:e]}...\n")
        return 0
    kind, pg = snap.find(a.quote or "")
    print(f"{kind} pages={pg}")
    return 0 if kind != "absent" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:  # e.g. piped into `head`
        sys.stderr.close()
        raise SystemExit(0)
