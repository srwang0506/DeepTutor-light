"""English submission draft: check submission_en.json and build comments_submission.html.

    python3 submission.py check WORK/submission_en.json [--review WORK/review.json] [--strict] [--json]
    python3 submission.py build WORK/submission_en.json OUT/comments_submission.html [--review WORK/review.json]
    python3 submission.py skeleton WORK/review.json WORK/submission_en.json   # start from the template

Writing rules: references/submission.md. Page template: assets/templates/comments_submission.html
(tokens {{BPR_*}}); KaTeX (MIT) from assets/vendor/katex is inlined so formulas render offline.
The review.json next to submission_en.json is used when --review is not given.

check  (errors): language, required fields, one paragraph per item, three levels per
       strength/weakness/question/justification, unique ids, links (id / from) to
       review.json comments, every major weakness covered, rating / confidence / subscores
       equal to review.json overall, balanced $ / $$, no Chinese, no internal ids
       (S01, W02, N03, ...), no process or fingerprint terms, no user-stance wording.
       (warnings, errors with --strict): length bands, short <= medium <= long,
       Unicode math outside $...$, recommended counts other than 3 / 6, major weakness
       outside the recommended set, ethics flag vs. ethics field.
Standard library only.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import neutrality  # noqa: E402

ASSETS = HERE.parent / "assets"
TEMPLATE = ASSETS / "templates" / "comments_submission.html"
SKELETON = ASSETS / "templates" / "submission_en.template.json"
KATEX = ASSETS / "vendor" / "katex"

LEVELS = ("short", "medium", "long")
LISTS = ("strengths", "weaknesses", "questions")
# words per level: (warn below, warn above)
BANDS = {"short": (12, 70), "medium": (35, 150), "long": (65, 320)}
DEFAULT_RECOMMEND = {"strengths": 3, "weaknesses": 6}
DEFAULT_FORM = [
    ["summary", "Summary"], ["soundness", "Soundness"], ["presentation", "Presentation"],
    ["contribution", "Contribution"], ["strengths", "Strengths"], ["weaknesses", "Weaknesses"],
    ["questions", "Questions"], ["ethics", "Flag For Ethics Review"], ["rating", "Rating"],
    ["justification", "Justification"], ["confidence", "Confidence"], ["code_of_conduct", "Code Of Conduct"],
]
TOKENS = ("TITLE", "VENUE", "VERSION", "DATE", "RULES", "PAYLOAD", "KATEX_CSS", "KATEX_JS")

ID_RE = re.compile(r"\b[SWQGKN]\d{2}\b")
CJK_RE = re.compile(r"[　-〿㐀-鿿＀-￯]")
INTERNAL_RE = re.compile(
    r"review\.json|submission_en|critique\.json|novelty-comparisons|evidence\.py|rules_sha256|rules fingerprint|"
    r"user_stance|symmetry check|subagent|independent critic",
    re.I)
# Unicode math that belongs in $...$
MATH_CHARS = re.compile(r"[α-ωΑ-Ω∑∏∫∂∇∞≈≠≤≥±×÷√∈∉⊂⊆∪∩→←↦]|[𝒜-𝓏]")


def words(s: str) -> int:
    return len(re.findall(r"\S+", s or ""))


def strip_math(s: str) -> tuple[str, bool]:
    """Remove $$...$$ and $...$ (not \\$); return (text, balanced)."""
    t = re.sub(r"(?<!\\)\$\$.+?(?<!\\)\$\$", " ", s or "", flags=re.S)
    t = re.sub(r"(?<!\\)\$(?!\s)[^$\n]+?(?<![\s\\])\$", " ", t)
    return t, "$" not in t.replace("\\$", "")


def texts(sub: dict):
    """Yield (where, text) for every piece of prose in the draft."""
    yield "summary", sub.get("summary", "")
    for k in LISTS:
        for it in sub.get(k) or []:
            yield f"{k}.{it.get('id')}.title", it.get("title", "")
            for lv in LEVELS:
                yield f"{k}.{it.get('id')}.{lv}", it.get(lv, "")
    for lv in LEVELS:
        yield f"justification.{lv}", (sub.get("justification") or {}).get(lv, "")


def _num(label) -> int | None:
    m = re.match(r"\s*(\d+)", str(label or ""))
    return int(m.group(1)) if m else None



def focus_rank(review: dict | None) -> dict:
    """v1.5 focus order per comment id: 0 cuts a core claim (or tier A), 1 narrows one (or tier B),
    2 other untiered issue, 3 tier C (presentation). Empty when the review has no tier/core_claims."""
    if not review:
        return {}
    comments = [c for c in review.get("comments") or [] if c.get("status") not in ("strength", "withdrawn")]
    claims = (review.get("overall") or {}).get("core_claims") or []
    if not claims and not any(c.get("tier") for c in comments):
        return {}
    impact: dict[str, str] = {}
    for cl in claims if isinstance(claims, list) else []:
        for k, v in (cl.get("impacts") or {}).items():
            if v == "cut" or (v == "narrow" and impact.get(k) != "cut"):
                impact[k] = v
    out = {}
    for c in comments:
        cid, t, im = c.get("id"), c.get("tier"), impact.get(c.get("id"))
        out[cid] = 0 if (im == "cut" or t == "A") else 1 if (im == "narrow" or t == "B") else 3 if t == "C" else 2
    return out

def check(sub: dict, review: dict | None = None, strict: bool = False) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    E, W = errors.append, warnings.append
    if not isinstance(sub, dict):
        return {"valid": False, "errors": ["submission must be a JSON object"], "warnings": []}
    if sub.get("language") != "en":
        E("language must be 'en'")
    if not str(sub.get("summary", "")).strip():
        E("summary missing")
    seen: set[str] = set()
    for k in LISTS:
        items = sub.get(k)
        if not isinstance(items, list):
            E(f"{k} must be a list")
            continue
        if not items and k != "questions":
            E(f"{k}: empty")
        for i, it in enumerate(items):
            iid = it.get("id") or f"{k}[{i}]"
            if not it.get("id"):
                E(f"{k}[{i}]: id missing")
            elif iid in seen:
                E(f"{iid}: duplicate id")
            seen.add(iid)
            if not str(it.get("title", "")).strip():
                W(f"{iid}: title missing (used in the candidate list)")
            n = [words(it.get(lv, "")) for lv in LEVELS]
            for lv, w in zip(LEVELS, n):
                if not str(it.get(lv, "")).strip():
                    E(f"{iid}: '{lv}' version missing")
                    continue
                lo, hi = BANDS[lv]
                if not lo <= w <= hi:
                    W(f"{iid}: {lv} has {w} words (expected {lo}-{hi})")
            if all(n) and not n[0] <= n[1] <= n[2]:
                W(f"{iid}: versions not ordered by length ({n[0]}/{n[1]}/{n[2]} words)")
    j = sub.get("justification")
    if not isinstance(j, dict):
        E("justification must have short / medium / long")
    else:
        for lv in LEVELS:
            if not str(j.get(lv, "")).strip():
                E(f"justification: '{lv}' version missing")

    for where, t in texts(sub):
        if not t:
            continue
        if re.search(r"\n\s*\n", t) and not where.startswith("summary"):
            E(f"{where}: one paragraph per item (blank line found)")
        if re.search(r"\n\s*\n", t) and where == "summary":
            E("summary: write one paragraph")
        plain, ok = strip_math(t)
        if not ok:
            E(f"{where}: unbalanced $ (inline math $...$, display math $$...$$; literal dollar \\$)")
        if CJK_RE.search(t):
            E(f"{where}: Chinese text in the English draft")
        m = ID_RE.search(plain)
        if m:
            E(f"{where}: internal id '{m.group(0)}' in the text")
        m = INTERNAL_RE.search(plain)
        if m:
            E(f"{where}: process term '{m.group(0)}' does not belong in the submitted text")
        m = MATH_CHARS.search(plain)
        if m:
            (E if strict else W)(f"{where}: '{m.group(0)}' outside math; write it in LaTeX inside $...$")
        for hit in neutrality.scan_text(t):
            E(f"{where}: stance wording '{hit}'")

    rec = sub.get("recommend") or {}
    for k, v in DEFAULT_RECOMMEND.items():
        if rec.get(k, v) != v:
            W(f"recommend.{k} = {rec.get(k)} (default {v}; say why in the delivery note)")

    scores = sub.get("scores") or {}
    for k in ("rating", "confidence"):
        if not str(scores.get(k, "")).strip():
            E(f"scores.{k} missing")
    form = sub.get("form") or DEFAULT_FORM
    for key, _label in form:
        if key not in ("summary", "justification") + LISTS and key not in scores:
            E(f"form field '{key}' has no value in scores")

    if review:
        ov = review.get("overall") or {}
        if ov.get("score") is not None and _num(scores.get("rating")) != ov.get("score"):
            E(f"scores.rating {scores.get('rating')!r} does not match overall.score {ov.get('score')}")
        if ov.get("confidence") is not None and _num(scores.get("confidence")) != ov.get("confidence"):
            E(f"scores.confidence does not match overall.confidence {ov.get('confidence')}")
        for k, v in (ov.get("subscores") or {}).items():
            if k in scores and _num(scores[k]) != v:
                E(f"scores.{k} {scores[k]!r} does not match overall.subscores.{k} {v}")
            elif k not in scores:
                W(f"overall.subscores.{k} not in scores")
        comments = {c.get("id"): c for c in review.get("comments") or []}
        covered: dict[str, int] = {}
        for k in LISTS:
            for i, it in enumerate(sub.get(k) or []):
                refs = it.get("from") or ([it.get("id")] if it.get("id") in comments else [])
                if not refs:
                    E(f"{it.get('id')}: not linked to review.json (set 'from': [comment ids])")
                for r in refs:
                    c = comments.get(r)
                    if c is None:
                        E(f"{it.get('id')}: from '{r}' is not a review.json comment")
                        continue
                    if c.get("status") == "withdrawn":
                        E(f"{it.get('id')}: from '{r}' was withdrawn in review.json")
                    is_strength = c.get("status") == "strength"
                    if (k == "strengths") != is_strength:
                        W(f"{it.get('id')}: listed under {k} but {r} has status {c.get('status')}")
                    if k == "weaknesses":
                        covered[r] = min(covered.get(r, 99), i)
        n_w = rec.get("weaknesses", DEFAULT_RECOMMEND["weaknesses"])
        for cid, c in comments.items():
            if c.get("severity") == "major" and c.get("status") in ("confirmed", "limitation") \
                    and c.get("status") != "withdrawn":
                if cid not in covered:
                    E(f"major {cid} ({c.get('title', '')[:30]}) is not covered by any weakness")
                elif covered[cid] >= n_w:
                    (E if strict else W)(f"major {cid} is outside the {n_w} recommended weaknesses; move it up")
        fr = focus_rank(review)
        if fr:
            ranks = []
            for it in sub.get("weaknesses") or []:
                refs = [r for r in (it.get("from") or []) if r in fr]
                ranks.append((it.get("id"), min(fr[r] for r in refs) if refs else 2))
            for (pid, pr), (nid, nr) in zip(ranks, ranks[1:]):
                if nr < pr:
                    W(f"v1.5 focus: {nid} (focus {nr}) comes after {pid} (focus {pr}); order weaknesses by impact on core claims")
            top = ranks[:n_w]
            if any(r == 3 for _, r in top) and any(r < 3 for _, r in ranks[n_w:]):
                W("v1.5 focus: a presentation-only (tier C) weakness is recommended while a core-impact weakness is folded")
            c_only = [i for i, r in ranks if r == 3]
            if len(c_only) > 1:
                W(f"v1.5 focus: {len(c_only)} presentation-only weaknesses {c_only}; merge them into one item")
        flagged = any(c.get("ethics_flag") for c in comments.values())
        eth = str(scores.get("ethics", ""))
        if flagged and re.search(r"\bno\b", eth, re.I):
            W("a comment has ethics_flag but scores.ethics says no ethics review is needed")

    if strict:
        errors += [f"[strict] {w}" for w in warnings if not w.startswith("v1.5")]
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "stats": {k: len(sub.get(k) or []) for k in LISTS}}


def katex_assets() -> tuple[str, str]:
    try:
        css = (KATEX / "katex.inline.css").read_text(encoding="utf-8")
        js = (KATEX / "katex.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
        return "<style>" + css + "</style>", "<script>" + js + "</script>"
    except OSError:
        return "", ""


def build(sub: dict, review: dict | None = None, template: Path = TEMPLATE) -> str:
    review = review or {}
    E = html.escape
    p = review.get("paper") or {}
    venue = review.get("venue") or {}
    prov = review.get("provenance") or {}
    vname = f"{venue.get('id') or ''} {venue.get('year') or ''}".strip() or "Conference"
    payload = dict(sub)
    payload.setdefault("recommend", dict(DEFAULT_RECOMMEND))
    payload.setdefault("form", DEFAULT_FORM)
    payload["key"] = "bpr-submission:" + str(p.get("title", "")) + ":" + str(review.get("review_date", ""))
    payload["file_title"] = str(p.get("title", "review"))
    rules = ""
    if prov:
        tag = "官方规则" if prov.get("rules_official") else "规则已修改"
        rules = (f'<span class="mi"><i class="ic ic-shield"></i>{tag} '
                 f'<code>{E(str(prov.get("rules_sha256", ""))[:12])}</code></span>')
    kcss, kjs = katex_assets()
    vals = {
        "TITLE": E(str(p.get("title", ""))), "VENUE": E(vname), "VERSION": E(str(p.get("version", ""))),
        "DATE": E(str(review.get("review_date", ""))), "RULES": rules,
        "PAYLOAD": json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"),
        "KATEX_CSS": kcss, "KATEX_JS": kjs,
    }
    out = template.read_text(encoding="utf-8")
    for t in TOKENS:
        out = out.replace("{{BPR_" + t + "}}", vals[t])
    left = re.findall(r"\{\{BPR_[A-Z_]+\}\}", out)
    if left:
        raise ValueError(f"template tokens not filled: {sorted(set(left))}")
    return out


def skeleton(review: dict) -> dict:
    """Template filled with ids, links and scores from review.json; prose left as TODO."""
    sk = json.loads(SKELETON.read_text(encoding="utf-8"))
    ov = review.get("overall") or {}
    comments = [c for c in review.get("comments") or [] if c.get("status") != "withdrawn"]
    rank = {"major": 0, "minor": 1, None: 2}
    todo = {lv: "TODO" for lv in LEVELS}

    def item(c, prefix, n):
        return {"id": f"{prefix}{n:02d}", "from": [c["id"]], "title": "TODO: " + str(c.get("title", ""))[:60],
                **({"severity": c.get("severity")} if c.get("severity") else {}), **todo}

    st = [c for c in comments if c.get("status") == "strength"]
    fr = focus_rank(review)
    wk = sorted([c for c in comments if c.get("status") in ("confirmed", "limitation", "suggestion")],
                key=lambda c: (fr.get(c.get("id"), 2), rank.get(c.get("severity"), 2)))
    qs = [c for c in comments if c.get("status") == "question"]
    sk["strengths"] = [item(c, "S", i + 1) for i, c in enumerate(st)]
    sk["weaknesses"] = [item(c, "W", i + 1) for i, c in enumerate(wk)]
    sk["questions"] = [item(c, "Q", i + 1) for i, c in enumerate(qs)]
    sc = sk.setdefault("scores", {})
    if ov.get("score") is not None:
        sc["rating"] = f"{ov['score']}: TODO venue label"
    if ov.get("confidence") is not None:
        sc["confidence"] = f"{ov['confidence']}: TODO venue label"
    subs = ov.get("subscores") or {}
    if subs:
        default_subs = ("soundness", "presentation", "contribution")
        for k in default_subs:
            if k not in subs:
                sc.pop(k, None)
        for k, v in subs.items():
            sc[k] = f"{v}: TODO venue label"
        rows = [r for r in sk.get("form", DEFAULT_FORM) if r[0] not in default_subs]
        sk["form"] = rows[:1] + [[k, k.replace("_", " ").title()] for k in subs] + rows[1:]
    return sk


def _load_review(sub_path: Path, review_arg: str | None) -> dict | None:
    p = Path(review_arg) if review_arg else sub_path.parent / "review.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _report(res: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    print(("通过" if res["valid"] else "未通过") + f"  {json.dumps(res['stats'], ensure_ascii=False)}")
    for e in res["errors"]:
        print("  错误:", e)
    for w in res["warnings"]:
        print("  警告:", w)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("check")
    c.add_argument("submission")
    c.add_argument("--review")
    c.add_argument("--strict", action="store_true")
    c.add_argument("--json", action="store_true")
    b = sp.add_parser("build")
    b.add_argument("submission")
    b.add_argument("out")
    b.add_argument("--review")
    b.add_argument("--force", action="store_true", help="write the page even if check fails")
    s = sp.add_parser("skeleton")
    s.add_argument("review")
    s.add_argument("out")
    a = ap.parse_args()

    if a.cmd == "skeleton":
        out = Path(a.out)
        if out.exists():
            print(f"{out} 已存在，未覆盖")
            return 1
        sk = skeleton(json.loads(Path(a.review).read_text(encoding="utf-8")))
        out.write_text(json.dumps(sk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("写出", out)
        return 0
    sp_ = Path(a.submission)
    sub = json.loads(sp_.read_text(encoding="utf-8"))
    review = _load_review(sp_, a.review)
    if a.cmd == "check":
        res = check(sub, review, a.strict)
        _report(res, a.json)
        return 0 if res["valid"] else 1
    res = check(sub, review)
    if not res["valid"] and not a.force:
        _report(res, False)
        print("未写出页面；修复错误后重试（或 --force）")
        return 1
    Path(a.out).write_text(build(sub, review), encoding="utf-8")
    print("写出", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
