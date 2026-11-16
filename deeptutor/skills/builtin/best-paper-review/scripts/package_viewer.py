"""Package PDF + review into ONE offline HTML file.

    python3 package_viewer.py paper.pdf WORK/review.json WORK/review-viewer.html            # reader (default)
    python3 package_viewer.py paper.pdf WORK/review.json WORK/review-editor.html --editor   # editable viewer

Default output is the read-only reader (assets/templates/pdf_reader): evidence quotes are
highlighted on the PDF text layer, margin markers and page thumbnails link to the comments,
and a "论文速览" view is built only from fields already in review.json. Formulas render with
KaTeX. This is what finalize.py writes as review-viewer.html.

--editor packages the editable viewer (assets/templates/pdf_viewer, the same UI as serve.py):
new comments, region boxes, undo, export of an annotated PDF. Edits are autosaved in the
browser; use 导出 -> 审稿 JSON to keep them.

Both embed the complete PDF (it contains the manuscript - share only when allowed), the
review JSON and the text snapshots; they open by double-click, no server needed.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import read_json  # noqa: E402

VIEWER = HERE.parent / "assets" / "templates" / "pdf_viewer"   # editor (serve.py, --editor)
READER = HERE.parent / "assets" / "templates" / "pdf_reader"   # read-only reader (default)
KATEX = HERE.parent / "assets" / "vendor" / "katex"


def sources_payload(review: dict, base: Path) -> dict:
    """Snapshot texts + page offsets for every source readable inside the review dir."""
    out = {}
    base_r = base.resolve()
    for s in review.get("sources", []):
        try:
            p = (base / s["path"]).resolve()
            p.relative_to(base_r)
            item = {"text": p.read_text(encoding="utf-8")}
            if s.get("manifest"):
                mp = (base / s["manifest"]).resolve()
                mp.relative_to(base_r)
                man = read_json(mp)
                item["pages"] = man.get("pages")
                item["injection_hits"] = man.get("injection_hits", [])
            out[s["id"]] = item
        except (KeyError, OSError, ValueError):
            continue
    return out


def venue_form(review: dict) -> dict | None:
    try:
        vj = read_json(HERE.parent / "assets" / "venues.json")
        vid = (review.get("venue") or {}).get("id")
        v = vj["venues"].get(vid) if vid else None
        return vj["forms"][v["form"] if v else "generic_conference"]
    except (OSError, KeyError):
        return None


def _safe_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build_editor(pdf: Path, review_path: Path, with_sources: bool = True) -> str:
    review = read_json(review_path)
    embed = {"pdf": base64.b64encode(pdf.read_bytes()).decode("ascii"), "review": review,
             "sources": sources_payload(review, review_path.parent) if with_sources else {}, "form": venue_form(review)}
    html = (VIEWER / "index.html").read_text(encoding="utf-8")
    css = (VIEWER / "viewer.css").read_text(encoding="utf-8")
    js = (VIEWER / "viewer.js").read_text(encoding="utf-8")
    vendor = {n: (VIEWER / "vendor" / n).read_text(encoding="utf-8") for n in ("pdf.min.js", "pdf-lib.min.js", "pdf.worker.min.js")}
    for name, code in vendor.items():
        if "</script" in code.lower():
            raise RuntimeError(f"{name} contains </script; cannot inline safely")
    worker = (f'<script id="bpr-worker" type="text/x-pdfjs-worker">{vendor["pdf.worker.min.js"]}</script>\n'
              "<script>window.__BPR_EMBED__.workerUrl=URL.createObjectURL(new Blob([document.getElementById('bpr-worker').textContent],"
              "{type:'text/javascript'}));</script>")
    html = html.replace('<link rel="stylesheet" href="viewer.css">', f"<style>{css}</style>")
    html = html.replace('<script src="vendor/pdf.min.js"></script>',
                        f"<script>window.__BPR_EMBED__={_safe_json(embed)};</script>\n{worker}\n<script>{vendor['pdf.min.js']}</script>")
    html = html.replace('<script src="vendor/pdf-lib.min.js"></script>', f"<script>{vendor['pdf-lib.min.js']}</script>")
    html = html.replace('<script src="viewer.js"></script>', f"<script>{js}</script>")
    return html



def build(pdf: Path, review_path: Path, with_sources: bool = True, editor: bool = False) -> str:
    """Single-file HTML: the reader by default, the editable viewer with editor=True."""
    if editor:
        return build_editor(pdf, review_path, with_sources)
    review = read_json(review_path)
    embed = {"pdf": base64.b64encode(pdf.read_bytes()).decode("ascii"), "review": review,
             "sources": sources_payload(review, review_path.parent) if with_sources else {}}
    html = (READER / "index.html").read_text(encoding="utf-8")
    css = (READER / "viewer.css").read_text(encoding="utf-8")
    js = (READER / "viewer.js").read_text(encoding="utf-8")
    pdfjs = (VIEWER / "vendor" / "pdf.min.js").read_text(encoding="utf-8")
    worker = (VIEWER / "vendor" / "pdf.worker.min.js").read_text(encoding="utf-8")
    kcss = (KATEX / "katex.inline.css").read_text(encoding="utf-8")
    kjs = (KATEX / "katex.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    for name, code in (("pdf.min.js", pdfjs), ("pdf.worker.min.js", worker)):
        if "</script" in code.lower():
            raise RuntimeError(name + " contains </script; cannot inline safely")
    boot = ("<script>window.__BPR_EMBED__=" + _safe_json(embed) + ";</script>\n"
            '<script id="bpr-worker" type="text/x-pdfjs-worker">' + worker + "</script>\n"
            "<script>window.__BPR_EMBED__.workerUrl=URL.createObjectURL(new Blob([document.getElementById('bpr-worker').textContent],"
            "{type:'text/javascript'}));</script>\n<script>" + pdfjs + "</script>")
    html = html.replace('<link rel="stylesheet" href="viewer.css">', "<style>" + kcss + "</style>\n<style>" + css + "</style>")
    html = html.replace('<script src="vendor/pdf.min.js"></script>', boot)
    html = html.replace('<script src="viewer.js"></script>', "<script>" + kjs + "</script>\n<script>" + js + "</script>")
    return html


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("review")
    ap.add_argument("out")
    ap.add_argument("--no-sources", action="store_true", help="do not embed snapshots (no in-browser quote checks)")
    ap.add_argument("--editor", action="store_true", help="package the editable viewer instead of the reader")
    a = ap.parse_args()
    html = build(Path(a.pdf), Path(a.review), not a.no_sources, editor=a.editor)
    Path(a.out).write_text(html, encoding="utf-8")
    print(f"写出 {a.out}（{len(html.encode('utf-8')) / 1e6:.1f} MB，内含完整 PDF，分享前请确认可以公开）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
