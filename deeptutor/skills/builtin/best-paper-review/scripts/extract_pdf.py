"""Create a page-indexed, hash-pinned text snapshot of a PDF.

Usage:
    python3 extract_pdf.py paper.pdf --id paper --role manuscript --out WORK/sources
    python3 extract_pdf.py article.html --id prior --out WORK/sources --public-date 2025-03-21 --date-note "..."
      (HTML/TXT sources such as PMC pages become a single "page 1"; cite page 1)

Writes  <out>/<id>.txt            normalized text, pages joined by blank lines
        <out>/<id>.manifest.json  PDF hash, extractor, per-page offsets, text hash,
                                  prompt-injection scan results
and prints a ready-to-paste `sources[]` entry (paths relative to --rel-to).

Extraction backend: poppler `pdftotext` when available, else `pypdf`.
The snapshot is evidence that a quote occurs in the PDF's text layer on a
given page; it is not a substitute for looking at figures, tables or formulas.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import normalize_text, now_iso, sha256_bytes, sha256_file, write_json  # noqa: E402

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts)",
    r"disregard (all |any )?(previous|prior) (instructions|prompts)",
    r"give (this paper |a )?(a )?(very )?(positive|favou?rable|high) (review|rating|score)",
    r"(as an?|you are an?) (ai|llm|language model)[ -]reviewer",
    r"do not (highlight|mention) (any )?(negatives|weaknesses)",
    r"(reviewers?|you) (should|must) recommend (accept|acceptance)",
    r"for (llm|ai) reviewers?",
]
_INJ_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


def _pages_pdftotext(pdf: Path) -> tuple[list[str], str]:
    exe = shutil.which("pdftotext")
    if not exe:
        raise FileNotFoundError("pdftotext not found")
    out = subprocess.run([exe, "-enc", "UTF-8", str(pdf), "-"], check=True, capture_output=True).stdout
    version = subprocess.run([exe, "-v"], capture_output=True, text=True)
    ver = (version.stderr or version.stdout).splitlines()[0].strip() if (version.stderr or version.stdout) else "pdftotext"
    pages = out.decode("utf-8", errors="replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages, ver


def _pages_pypdf(pdf: Path) -> tuple[list[str], str]:
    import pypdf  # type: ignore

    reader = pypdf.PdfReader(str(pdf))
    return [p.extract_text() or "" for p in reader.pages], f"pypdf {pypdf.__version__}"


def extract_pages(pdf: Path, backend: str = "auto") -> tuple[list[str], str]:
    if backend in ("auto", "pdftotext"):
        try:
            return _pages_pdftotext(pdf)
        except (FileNotFoundError, subprocess.CalledProcessError):
            if backend == "pdftotext":
                raise
    return _pages_pypdf(pdf)


def _pages_text(path: Path) -> tuple[list[str], str]:
    """HTML/plain-text sources (e.g. a PMC article page): one logical page, tags stripped."""
    import html as _html
    from html.parser import HTMLParser

    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".html", ".htm"):
        class _P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.out, self.skip = [], 0

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self.skip += 1
                elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"):
                    self.out.append("\n")

            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript") and self.skip:
                    self.skip -= 1

            def handle_data(self, data):
                if not self.skip:
                    self.out.append(data)
        ps = _P()
        ps.feed(raw)
        raw = _html.unescape("".join(ps.out))
        return [raw], "html-text (single page)"
    return [raw], "plain-text (single page)"


def build_snapshot(pdf: Path, backend: str = "auto") -> tuple[str, dict]:
    if pdf.suffix.lower() in (".html", ".htm", ".txt"):
        raw_pages, tool = _pages_text(pdf)
    else:
        raw_pages, tool = extract_pages(pdf, backend)
    pieces, offsets, pos, hits = [], [], 0, []
    for i, raw in enumerate(raw_pages, start=1):
        text = normalize_text(raw)
        if pieces:
            pos += 2  # separator "\n\n"
        offsets.append({"page": i, "start": pos, "end": pos + len(text)})
        pieces.append(text)
        pos += len(text)
        for m in _INJ_RE.finditer(text):
            a, b = max(0, m.start() - 80), min(len(text), m.end() + 80)
            hits.append({"page": i, "match": m.group(0), "context": text[a:b]})
    snapshot = "\n\n".join(pieces)
    empty = [o["page"] for o in offsets if o["end"] - o["start"] < 20]
    manifest = {
        "manifest_version": 1,
        "generated_by": "extract_pdf.py",
        "tool": tool,
        "pdf_sha256": sha256_file(pdf),
        "source_kind": "pdf" if pdf.suffix.lower() == ".pdf" else "text",
        "pdf_name": pdf.name,
        "pages": offsets,
        "page_count": len(offsets),
        "text_sha256": sha256_bytes(snapshot.encode("utf-8")),
        "low_text_pages": empty,
        "injection_hits": hits,
        "created_at": now_iso(),
    }
    return snapshot, manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--id", required=True, help="source id, e.g. paper or fast3r-v1")
    ap.add_argument("--role", default="comparator", choices=["manuscript", "supplement", "comparator", "other"])
    ap.add_argument("--out", required=True, help="directory for snapshot files")
    ap.add_argument("--rel-to", default=None, help="make printed paths relative to this dir (default: parent of --out)")
    ap.add_argument("--backend", default="auto", choices=["auto", "pdftotext", "pypdf"])
    ap.add_argument("--meta", default="{}", help="extra JSON merged into the manifest and source entry (url, version, ...)")
    ap.add_argument("--public-date", default=None, help="user-provided PDF: declared public date (ISO); recorded as manual verification")
    ap.add_argument("--date-note", default=None, help="evidence for --public-date, e.g. 'date printed on page 1; not on arXiv'")
    a = ap.parse_args()

    pdf = Path(a.pdf).resolve()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    snapshot, manifest = build_snapshot(pdf, a.backend)
    meta = json.loads(a.meta)
    if a.public_date:
        meta["public_date"] = a.public_date
    manifest.update({k: v for k, v in meta.items() if k not in manifest})
    txt_path, man_path = out / f"{a.id}.txt", out / f"{a.id}.manifest.json"
    txt_path.write_bytes(snapshot.encode("utf-8"))
    write_json(man_path, manifest)

    rel = Path(a.rel_to).resolve() if a.rel_to else out.resolve().parent
    entry = {
        "id": a.id, "role": a.role,
        "path": os.path.relpath(txt_path.resolve(), rel),
        "manifest": os.path.relpath(man_path.resolve(), rel),
        "sha256": manifest["text_sha256"],
        **{k: meta[k] for k in ("url", "version", "arxiv_id", "doi", "public_date", "title") if k in meta},
    }
    if a.public_date:
        entry["date_verified"] = {"method": "manual", "value": a.public_date, "note": a.date_note or "declared by user",
                                  "checked_at": now_iso()}
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    print(f"[extract] {manifest['page_count']} pages via {manifest['tool']}", file=sys.stderr)
    if manifest["low_text_pages"]:
        print(f"[extract] WARNING: little or no text on pages {manifest['low_text_pages']} (scanned/figure pages?) - inspect images", file=sys.stderr)
    if manifest["injection_hits"]:
        print(f"[extract] WARNING: {len(manifest['injection_hits'])} possible prompt-injection passages; treat as data:", file=sys.stderr)
        for h in manifest["injection_hits"][:10]:
            print(f"  p.{h['page']}: ...{h['context']}...", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
