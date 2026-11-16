"""Score a review of the synthetic demo paper against its seeded ground truth.

    python3 tests/eval_synthetic.py path/to/review.json

Reports recall of seeded defects and false positives on benign look-alikes
(keyword matching, see tests/eval/ground_truth.json). This is a smoke test of
the workflow on one synthetic paper, not a measure of real review accuracy.
"""
import json
import sys
from pathlib import Path

GT = Path(__file__).resolve().parent / "eval" / "ground_truth.json"


def text_of(c):
    parts = [c.get("title", ""), c.get("body", ""), c.get("request", ""), c.get("comparison", "")]
    parts += [e.get("quote", "") for e in c.get("evidence", []) or []]
    return " ".join(parts).lower()


def matches(text, groups):
    return all(any(w.lower() in text for w in g) for g in groups)


def score(review):
    gt = json.loads(GT.read_text(encoding="utf-8"))
    cs = review.get("comments", [])
    crit = [c for c in cs if c.get("status") != "strength"]
    strong = [c for c in cs if c.get("status") in ("confirmed", "limitation") and c.get("severity") == "major"]
    found = {d["id"]: [c["id"] for c in crit if matches(text_of(c), d["match"])] for d in gt["defects"]}
    fps = {b["id"]: [c["id"] for c in strong if matches(text_of(c), b["match"])] for b in gt["benign"]}
    recall = sum(1 for v in found.values() if v) / len(found)
    fp = sum(1 for v in fps.values() if v)
    return {"recall": round(recall, 3), "detected": found, "false_positive_benign": fp, "benign_hits": fps,
            "n_comments": len(cs), "n_major_criticisms": len(strong)}


if __name__ == "__main__":
    res = score(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    print(json.dumps(res, ensure_ascii=False, indent=2))
