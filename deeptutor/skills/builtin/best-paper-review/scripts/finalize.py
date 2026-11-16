"""Arrange a finished review into the standard output folder.

    python3 finalize.py WORK --pdf paper.pdf --out reviews/ [--dry-run] [--force]

Creates  <out>/<YYYYMMDD>_<Paper_Title>/  (suffix _2, _3, ... when it already exists):

    comments_submission.html     English submission draft (submission.py build)
    review-viewer.html           PDF reader: highlighted evidence, comments, 论文速览, offline (package_viewer.py)
    source/                      review.json, review.md, form.md, submission_en.json
    review_process_files/        contributions, novelty, critique, source snapshots, other work files

Only the two HTML files are meant to be opened; no review.html is produced. WORK is copied,
never moved or deleted. Before writing, evidence.py --strict and submission.py check must
pass (--force writes anyway and lists the failures in the console). If policy.submission_skipped
is set (with a reason), the folder has no comments_submission.html.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evidence  # noqa: E402
import package_viewer  # noqa: E402
import render  # noqa: E402
import submission  # noqa: E402

SOURCE_FILES = ("review.json", "submission_en.json")
SKIP = {"review.html", "review.md", "form.md", "review-viewer.html", "comments_submission.html",
        "paper.pdf", ".DS_Store"}


def folder_name(review: dict) -> str:
    date = re.sub(r"\D", "", str(review.get("review_date", "")))[:8]
    if len(date) != 8:
        raise ValueError("review_date must be YYYY-MM-DD")
    title = unicodedata.normalize("NFKD", str((review.get("paper") or {}).get("title", "")))
    title = title.encode("ascii", "ignore").decode()
    title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    if not title:
        raise ValueError("paper.title is empty or has no ASCII letters")
    return f"{date}_{title[:120].rstrip('_')}"


def target_dir(out: Path, name: str) -> Path:
    d = out / name
    n = 2
    while d.exists():
        d = out / f"{name}_{n}"
        n += 1
    return d


def plan(work: Path, review: dict) -> dict:
    """What goes where (relative paths inside WORK)."""
    process = []
    for p in sorted(work.iterdir()):
        if p.name in SOURCE_FILES or p.name in SKIP or p.name.startswith("."):
            continue
        process.append(p.name)
    has_sub = (work / "submission_en.json").exists()
    return {"source": [f for f in SOURCE_FILES if (work / f).exists()], "process": process,
            "submission": has_sub}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True, help="parent folder, e.g. reviews/")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    work, pdf, out = Path(a.work), Path(a.pdf), Path(a.out)
    review = json.loads((work / "review.json").read_text(encoding="utf-8"))
    pol = review.get("policy") or {}

    problems = []
    ev = evidence.validate(review, work, strict=True)
    problems += [f"evidence: {e}" for e in ev["errors"]]
    sub = None
    if (work / "submission_en.json").exists():
        sub = json.loads((work / "submission_en.json").read_text(encoding="utf-8"))
        problems += [f"submission: {e}" for e in submission.check(sub, review)["errors"]]
    elif not pol.get("submission_skipped"):
        problems.append("submission: WORK/submission_en.json missing (submission.py skeleton; "
                        "or set policy.submission_skipped with a reason)")
    if not pdf.exists():
        problems.append(f"pdf not found: {pdf}")

    dest = target_dir(out, folder_name(review))
    pl = plan(work, review)
    print("输出目录:", dest)
    print("  source/:", ", ".join(pl["source"] + ["review.md", "form.md"]))
    print("  review_process_files/:", ", ".join(pl["process"]) or "(无)")
    print("  comments_submission.html:", "是" if sub else "否（" + str(pol.get("submission_skipped") or "缺少") + "）")
    for p_ in problems:
        print("  问题:", p_)
    if problems and not a.force:
        print("未写出；修复以上问题后重试（或 --force）")
        return 1
    if a.dry_run:
        return 0

    src = dest / "source"
    proc = dest / "review_process_files"
    src.mkdir(parents=True)
    proc.mkdir()
    for f in pl["source"]:
        shutil.copy2(work / f, src / f)
    (src / "review.md").write_text(render.to_markdown(review), encoding="utf-8")
    (src / "form.md").write_text(render.to_form(review, None), encoding="utf-8")
    for name in pl["process"]:
        s = work / name
        (shutil.copytree if s.is_dir() else shutil.copy2)(s, proc / name)
    (dest / "review-viewer.html").write_text(package_viewer.build(pdf, work / "review.json"), encoding="utf-8")
    if sub is not None:
        (dest / "comments_submission.html").write_text(submission.build(sub, review), encoding="utf-8")
    print("完成:", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
