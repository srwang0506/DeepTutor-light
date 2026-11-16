"""Deterministic integrity checks for review.json (schema v2).

Usage:
    python3 evidence.py WORK/review.json [--strict] [--json]

Checks (errors unless noted):
  * snapshots: file hash, manifest produced by extract_pdf.py, manifest text hash,
    manuscript PDF hash = paper.pdf_sha256 = hash of paper.pdf (if present), page count
  * quotes: every evidence quote occurs in its source; its declared page matches
  * comments: enums, anchors, region bounds, confirmed-criticism requirements,
    novelty requirements (both sides quoted, comparator dated on/before cutoff)
  * dimensions: all nine assessed (quick mode may leave some unchecked if declared)
  * overall: score belongs to the venue scale, confidence 1-5, decisive ids exist
  * numeric_checks: expressions recomputed and compared with the reported value
  * submission_en.json (next to review.json): submission.py check; missing in full/self
    mode is a warning (error with --strict) unless policy.submission_skipped gives a reason
Warnings (errors with --strict): missing evidence pages, unverified comparator dates,
prompt-injection hits, unverified venue form, suspicious wording.

Passing means the record is internally consistent and the quotes are real.
It does NOT mean that a quote entails the comment, or that the review is right.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import operator
import sys
from bisect import bisect_right
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import integrity  # noqa: E402
import neutrality  # noqa: E402
from common import (CONFIDENCES, DIMENSIONS, NOVELTY_LEVELS, SCOPES, SEVERITIES, STATUSES,  # noqa: E402
                    normalize_text, parse_date, read_json, sha256_bytes, sha256_file)

ALLOWED_GENERATORS = {"extract_pdf.py"}
MODES = {"quick", "full", "self", "rebuttal"}
VAGUE = ["创新性有限", "实验不充分", "写作需要改进", "novelty is limited", "not novel enough"]


# ------------------------------------------------------------------ quote index
class Snapshot:
    def __init__(self, text: str, pages: list[dict] | None):
        self.text = text
        self.pages = pages or []
        self._starts = [p["start"] for p in self.pages]
        loose_chars, self._loose_map = [], []
        for i, ch in enumerate(text):
            if not ch.isspace() and ch != "-":
                loose_chars.append(ch.lower())
                self._loose_map.append(i)
        self.loose = "".join(loose_chars)

    def page_of(self, offset: int) -> int | None:
        if not self.pages:
            return None
        i = bisect_right(self._starts, offset) - 1
        return self.pages[i]["page"] if i >= 0 else None

    def find(self, quote: str) -> tuple[str, list[int]]:
        """Return (match_type, pages) where match_type is exact|loose|absent."""
        q = normalize_text(quote)
        if not q:
            return "absent", []
        hits, start = [], 0
        while (i := self.text.find(q, start)) != -1:
            hits.append(i)
            start = i + 1
        kind = "exact"
        if not hits:
            lq = "".join(ch.lower() for ch in q if not ch.isspace() and ch != "-")
            if len(lq) < 12:
                return "absent", []
            start = 0
            while (i := self.loose.find(lq, start)) != -1:
                hits.append(self._loose_map[i])
                start = i + 1
            kind = "loose"
        if not hits:
            return "absent", []
        return kind, sorted({p for p in (self.page_of(h) for h in hits) if p is not None})


# ------------------------------------------------------------------ arithmetic
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "sqrt": math.sqrt, "log": math.log}


def safe_eval(expr: str) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(f"disallowed expression element: {ast.dump(n)[:60]}")
    return float(ev(ast.parse(expr, mode="eval")))


# ------------------------------------------------------------------ validator

def score_range_check(core_claims, ids, score, significance, six_point=True):
    """v1.5.1 (calibration.md G7): the score range follows the core-claim verdicts only.

    Returns warning strings: impacts that contradict verdict/tier, and a score outside the range.
    """
    out = []
    verdicts = []
    core_b_narrow = False
    for i, x in enumerate(core_claims):
        v = x.get("verdict")
        if v not in ("holds", "partly", "fails"):
            return out
        verdicts.append(v)
        for k, imp in (x.get("impacts") or {}).items():
            tier = (ids.get(k) or {}).get("tier")
            if imp == "cut" and tier == "C":
                out.append(f"v1.5.1: core_claims[{i}].impacts[{k}] is cut but {k} is tier C (C cannot cut an evidence chain)")
            if imp == "narrow" and tier == "A":
                out.append(f"v1.5.1: core_claims[{i}].impacts[{k}] is narrow but {k} is tier A (A on a core claim cuts; re-tier or re-judge)")
            if imp == "cut" and v == "holds":
                out.append(f"v1.5.1: core_claims[{i}] verdict holds although {k} cuts its evidence chain")
            if imp == "cut" and tier == "A" and v != "fails":
                out.append(f"v1.5.1: core_claims[{i}] has tier-A cut {k}; verdict should be fails")
            if imp == "narrow" and tier == "B":
                core_b_narrow = True
    if not verdicts or not six_point or not isinstance(score, int):
        return out
    if "fails" in verdicts:
        lo, hi, band = 1, 2, "a core claim fails"
    elif "partly" in verdicts:
        lo, hi, band = 2, 3, "a core claim partly holds"
    elif core_b_narrow:
        lo, hi, band = 3, 4, "all core claims hold, a narrowing B issue remains"
    else:
        lo, hi, band = 4, 6, "all core claims hold, no B issue on the core"
        if score >= 5 and significance != "high":
            out.append(f"v1.5.1 G7: score {score} needs significance high")
    if significance == "low":
        lo -= 1
    if not lo <= score <= hi:
        out.append(f"v1.5.1 G7: score {score} outside {lo}-{hi} ({band}); re-check the verdicts rather than the score")
    return out


def validate(data, base: Path, strict: bool = False, venues_path: Path | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    E, W = errors.append, warnings.append
    if not isinstance(data, dict):
        return {"valid": False, "errors": ["review must be a JSON object"], "warnings": []}
    if data.get("schema_version") != 2:
        E("schema_version must be 2")
    mode = data.get("mode", "full")
    if mode not in MODES:
        E(f"mode must be one of {sorted(MODES)}")
    cutoff = parse_date(data.get("cutoff"))
    if cutoff is None:
        E("cutoff must be an ISO date")
    paper = data.get("paper") if isinstance(data.get("paper"), dict) else {}
    if not paper.get("title"):
        E("paper.title missing")

    # ---------------- sources
    srcs: dict[str, dict] = {}
    snaps: dict[str, Snapshot] = {}
    raw_sources = data.get("sources", [])
    if not isinstance(raw_sources, list):
        E("sources must be a list")
        raw_sources = []
    base_r = base.resolve()
    manuscript_ids = []
    for s in raw_sources:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"]:
            E("source without id")
            continue
        sid = s["id"]
        if sid in srcs:
            E(f"duplicate source id {sid}")
            continue
        srcs[sid] = s
        role = s.get("role")
        if role not in ("manuscript", "supplement", "comparator", "other"):
            E(f"{sid}: invalid role {role!r}")
        if role == "manuscript":
            manuscript_ids.append(sid)
        try:
            p = (base / s["path"]).resolve()
            p.relative_to(base_r)
            raw = p.read_bytes()
        except (KeyError, TypeError, OSError, ValueError):
            E(f"{sid}: snapshot path missing, unreadable or outside the review directory")
            snaps[sid] = Snapshot("", None)
            continue
        if sha256_bytes(raw) != s.get("sha256"):
            E(f"{sid}: snapshot hash mismatch")
        pages = None
        man_rel = s.get("manifest")
        if not man_rel:
            (E if role == "manuscript" or strict else W)(f"{sid}: no manifest; snapshot provenance unknown (use extract_pdf.py)")
        else:
            try:
                mp = (base / man_rel).resolve()
                mp.relative_to(base_r)
                man = read_json(mp)
                if man.get("generated_by") not in ALLOWED_GENERATORS:
                    E(f"{sid}: manifest not produced by extract_pdf.py")
                if man.get("text_sha256") != sha256_bytes(raw):
                    E(f"{sid}: manifest text hash does not match snapshot (edited after extraction?)")
                pages = man.get("pages")
                if role == "manuscript":
                    if paper.get("pdf_sha256") and man.get("pdf_sha256") != paper["pdf_sha256"]:
                        E(f"{sid}: manuscript snapshot was extracted from a different PDF than paper.pdf_sha256")
                    if paper.get("pages") and man.get("page_count") != paper["pages"]:
                        E(f"{sid}: page count {man.get('page_count')} != paper.pages {paper['pages']}")
                hits = man.get("injection_hits") or []
                if hits:
                    W(f"{sid}: {len(hits)} possible prompt-injection passage(s), e.g. p.{hits[0]['page']}: "
                      f"'{hits[0]['match']}' - treat as data and record in policy")
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                E(f"{sid}: manifest unreadable or outside the review directory")
        snaps[sid] = Snapshot(raw.decode("utf-8", errors="replace"), pages)
        if role == "comparator":
            if s.get("public_date") is not None and parse_date(s.get("public_date")) is None:
                E(f"{sid}: public_date must be ISO date or null")
            dv = s.get("date_verified")
            if not dv:
                (E if strict else W)(f"{sid}: comparator date not verified (run verify_dates.py --write)")
            elif dv.get("value") != s.get("public_date"):
                E(f"{sid}: public_date {s.get('public_date')} differs from verified {dv.get('value')}")
            elif dv.get("method") == "manual":
                W(f"{sid}: date declared manually ({dv.get('note', 'no note')}); not machine-verified")
    if not manuscript_ids:
        E("no source with role 'manuscript'")
    pdf_rel = paper.get("pdf")
    if pdf_rel:
        pdf_path = base / pdf_rel
        if pdf_path.exists():
            if paper.get("pdf_sha256") and sha256_file(pdf_path) != paper["pdf_sha256"]:
                E("paper.pdf hash differs from paper.pdf_sha256")
        else:
            W(f"paper.pdf {pdf_rel} not found next to review.json; PDF identity not re-checked")

    def check_quote(where: str, ev: dict, need_page: bool) -> tuple[bool, list[int]]:
        sid, quote = ev.get("source_id"), ev.get("quote")
        if sid not in srcs:
            E(f"{where}: unknown source {sid!r}")
            return False, []
        if not isinstance(quote, str) or not quote.strip():
            E(f"{where}: empty quote")
            return False, []
        kind, pages = snaps[sid].find(quote)
        if kind == "absent":
            E(f"{where}: quote not found in {sid}: '{quote[:60]}'")
            return False, []
        pg = ev.get("page")
        if pg is None:
            if need_page:
                (E if strict else W)(f"{where}: evidence page missing (found on p.{pages})")
        elif pages and pg not in pages:
            E(f"{where}: quote is on page(s) {pages}, not p.{pg}")
        return True, pages

    # ---------------- contributions
    contribs = ((data.get("summary") or {}).get("contributions")) or []
    cids = set()
    for c in contribs:
        cid = c.get("id")
        cids.add(cid)
        if c.get("quote"):
            check_quote(f"contribution {cid}", {"source_id": manuscript_ids[0] if manuscript_ids else None,
                                                 "quote": c["quote"], "page": c.get("page")}, True)
        if c.get("novelty_assessment") not in (None, *NOVELTY_LEVELS):
            E(f"contribution {cid}: invalid novelty_assessment")
    if mode in ("full", "self") and not contribs:
        E("summary.contributions is empty (list atomic contributions C1..Cn)")

    # ---------------- numeric checks
    nchecks = {}
    for n in data.get("numeric_checks", []) or []:
        nid = n.get("id")
        nchecks[nid] = n
        try:
            val = safe_eval(str(n.get("expression")))
        except Exception as e:  # noqa: BLE001
            E(f"numeric {nid}: cannot evaluate expression ({e})")
            continue
        try:
            rep = float(n.get("value", n.get("reported")))
        except (TypeError, ValueError):
            E(f"numeric {nid}: 'value' (your recomputed result) must be numeric")
            continue
        tol = float(n.get("tolerance", max(0.05, abs(rep) * 0.005)))
        if abs(val - rep) > tol:
            E(f"numeric {nid}: expression = {val:.6g}, but value = {rep} (tolerance {tol})")
        if n.get("claimed") is not None:
            try:
                cl = float(n["claimed"])
                n["_claimed_matches"] = abs(val - cl) <= tol
            except (TypeError, ValueError):
                E(f"numeric {nid}: 'claimed' (the paper's number) must be numeric")
        for j, ev in enumerate(n.get("evidence", []) or []):
            check_quote(f"numeric {nid} evidence[{j}]", ev, False)

    # ---------------- comments
    comments = data.get("comments", [])
    if not isinstance(comments, list):
        E("comments must be a list")
        comments = []
    ids: dict[str, dict] = {}
    for c in comments:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"]:
            E("comment without id")
            continue
        cid = c["id"]
        if cid in ids:
            E(f"{cid}: duplicate comment id")
        ids[cid] = c
        st, dim, scope = c.get("status"), c.get("dimension"), c.get("scope")
        if st not in STATUSES:
            E(f"{cid}: invalid status {st!r}")
        if dim not in DIMENSIONS:
            E(f"{cid}: invalid dimension {dim!r}")
        if st != "strength" and c.get("severity") not in SEVERITIES:
            E(f"{cid}: severity must be major/minor")
        if st != "strength" and c.get("tier") is not None and c.get("tier") not in ("A", "B", "C"):
            E(f"{cid}: tier must be A/B/C (calibration.md G1)")
        if c.get("confidence") not in (None, *CONFIDENCES):
            E(f"{cid}: invalid confidence")
        if not c.get("title") or not c.get("body"):
            E(f"{cid}: title and body required")
        if scope not in SCOPES:
            E(f"{cid}: invalid scope {scope!r}")
        if scope in ("page", "region"):
            pg = c.get("page")
            if type(pg) is not int or pg < 1 or (paper.get("pages") and pg > paper["pages"]):
                E(f"{cid}: invalid page {pg!r}")
        if scope == "region":
            r = c.get("rect")
            ok = (isinstance(r, list) and len(r) == 4 and all(type(v) in (int, float) and 0 <= v <= 1 for v in r)
                  and r[2] > 0 and r[3] > 0 and r[0] + r[2] <= 1.000001 and r[1] + r[3] <= 1.000001)
            if not ok:
                E(f"{cid}: invalid rect (normalized [x,y,w,h] inside the page)")
        evs = c.get("evidence", []) or []
        if not isinstance(evs, list) or any(not isinstance(e, dict) for e in evs):
            E(f"{cid}: evidence must be a list of objects")
            evs = []
        man_pages: set[int] = set()
        for j, ev in enumerate(evs):
            ok, pages = check_quote(f"{cid} evidence[{j}]", ev, srcs.get(ev.get("source_id"), {}).get("role") == "manuscript")
            if ok and srcs[ev["source_id"]].get("role") == "manuscript":
                man_pages.update([ev["page"]] if ev.get("page") else pages)
        for nid in c.get("numeric_check_ids", []) or []:
            if nid not in nchecks:
                E(f"{cid}: unknown numeric check {nid}")
        body = f"{c.get('title', '')} {c.get('body', '')}"
        if st in ("confirmed", "limitation") and len(c.get("body", "")) < 60 and any(v in body for v in VAGUE):
            W(f"{cid}: vague criticism; name the comparator / missing evidence / location (see calibration.md)")
        if st == "confirmed":
            if not man_pages:
                E(f"{cid}: confirmed issue needs at least one manuscript quote")
            if not str(c.get("countercheck", "")).strip():
                E(f"{cid}: confirmed issue needs a countercheck (strongest defense and why it fails)")
            if scope in ("page", "region") and man_pages and c.get("page") not in man_pages:
                (E if strict else W)(f"{cid}: anchored on p.{c.get('page')} but manuscript evidence is on p.{sorted(man_pages)}")
            if c.get("severity") == "major" and not str(c.get("request", "")).strip():
                W(f"{cid}: major issue without a concrete request to the authors")
        if st == "confirmed" and dim == "novelty":
            refs = c.get("comparator_ids") or []
            if not isinstance(refs, list) or not refs:
                E(f"{cid}: confirmed novelty issue needs comparator_ids")
                refs = []
            if not str(c.get("comparison", "")).strip():
                E(f"{cid}: confirmed novelty issue needs an explicit mechanism comparison")
            for rid in refs:
                s = srcs.get(rid)
                if not s:
                    E(f"{cid}: unknown comparator {rid}")
                    continue
                d = parse_date(s.get("public_date"))
                if d is None:
                    E(f"{cid}: comparator {rid} has unknown date -> must stay unresolved")
                elif cutoff and d > cutoff:
                    E(f"{cid}: comparator {rid} ({d}) is after the cutoff {cutoff}")
                if not any(e.get("source_id") == rid for e in evs):
                    E(f"{cid}: comparator {rid} is not quoted")
        if mode == "rebuttal" and st != "strength" and c.get("resolution") not in ("resolved", "partial", "unresolved", None):
            E(f"{cid}: invalid resolution")

    # ---------------- dimensions
    dims = data.get("dimensions") or {}
    not_done = " ".join(map(str, (data.get("policy") or {}).get("not_done", []) or []))
    for key, label in DIMENSIONS.items():
        d = dims.get(key)
        if not isinstance(d, dict):
            E(f"dimension {key} ({label}) not assessed")
            continue
        st = d.get("status")
        if st == "issues":
            linked = d.get("comment_ids") or []
            if not linked:
                E(f"dimension {key}: status 'issues' but no comment_ids")
            for cid in linked:
                if cid not in ids:
                    E(f"dimension {key}: unknown comment {cid}")
                elif ids[cid].get("dimension") != key:
                    W(f"dimension {key}: comment {cid} is tagged {ids[cid].get('dimension')}")
        elif st in ("ok", "na"):
            if len(str(d.get("note", "")).strip()) < 8:
                E(f"dimension {key}: '{st}' needs a note saying what was checked / why not applicable")
        elif st == "unchecked":
            if mode != "quick":
                E(f"dimension {key}: unchecked is only allowed in quick mode")
            elif label not in not_done and key not in not_done:
                E(f"dimension {key}: unchecked but not declared in policy.not_done")
        else:
            E(f"dimension {key}: invalid status {st!r}")
    for cid, c in ids.items():
        dim = c.get("dimension")
        if c.get("status") != "strength" and dim in dims and dims[dim].get("status") in ("ok", "na"):
            W(f"{cid}: criticism in dimension '{dim}' which is marked {dims[dim]['status']}")

    # ---------------- overall / venue
    ov = data.get("overall")
    venues_file = venues_path or HERE.parent / "assets" / "venues.json"
    form = None
    if isinstance(data.get("venue"), dict) and data["venue"].get("id"):
        try:
            vj = read_json(venues_file)
            v = vj["venues"].get(data["venue"]["id"])
            if v is None:
                E(f"venue {data['venue']['id']!r} not in assets/venues.json")
            else:
                form = vj["forms"][v["form"]]
                if not form.get("verified"):
                    W(f"venue form '{v['form']}' is not verified for this year; check {form.get('guide_url', 'the official guide')}")
        except (OSError, KeyError, json.JSONDecodeError):
            W("assets/venues.json unavailable; venue scale not checked")
    if mode != "quick" or ov:
        if not isinstance(ov, dict):
            E("overall missing")
        else:
            if not ov.get("recommendation") or not ov.get("justification"):
                E("overall needs recommendation and justification")
            conf = ov.get("confidence")
            if not (isinstance(conf, int) and 1 <= conf <= 5):
                E("overall.confidence must be an integer 1-5")
            if form:
                allowed = [x[0] for x in form["overall_scale"]]
                if ov.get("score") not in allowed:
                    E(f"overall.score {ov.get('score')!r} not on the venue scale {allowed}")
                for k, val in (ov.get("subscores") or {}).items():
                    if k not in form.get("subscores", {}):
                        W(f"overall.subscores.{k} not in the venue form")
                    elif val not in [x[0] for x in form["subscores"][k]]:
                        E(f"overall.subscores.{k}={val!r} not on scale")
                missing = [k for k in form.get("subscores", {}) if k not in (ov.get("subscores") or {})]
                if missing:
                    W(f"overall.subscores missing {missing}")
            for cid in ov.get("decisive_ids", []) or []:
                if cid not in ids:
                    E(f"overall.decisive_ids: unknown comment {cid}")
            if not ov.get("decisive_ids"):
                W("overall.decisive_ids empty: say which comments decide the recommendation")
            # v1.5 scoring rules (calibration.md G1-G13); warnings only while the rules are being validated
            if mode in ("full", "self"):
                untiered = [c.get("id") for c in comments if c.get("status") != "strength" and not c.get("tier")]
                if untiered:
                    W(f"v1.5: {len(untiered)} comments have no tier (A/B/C), e.g. {untiered[:3]}")
                cc = ov.get("core_claims")
                if not cc:
                    W("v1.5: overall.core_claims missing (core-contribution verdicts, calibration.md G6)")
                else:
                    for i, x in enumerate(cc if isinstance(cc, list) else []):
                        if x.get("verdict") not in ("holds", "partly", "fails"):
                            E(f"overall.core_claims[{i}].verdict must be holds/partly/fails")
                        for k, v in (x.get("impacts") or {}).items():
                            if k not in ids:
                                E(f"overall.core_claims[{i}].impacts: unknown comment {k}")
                            elif v not in ("cut", "narrow", "none"):
                                E(f"overall.core_claims[{i}].impacts[{k}] must be cut/narrow/none")
                    if isinstance(cc, list):
                        for w in score_range_check(cc, ids, ov.get("score"), ov.get("significance"),
                                                   bool(form and [x[0] for x in form["overall_scale"]] == [1, 2, 3, 4, 5, 6])):
                            W(w)
                if ov.get("significance") is not None and ov.get("significance") not in ("high", "medium", "low"):
                    E("overall.significance must be high/medium/low")
                if ov.get("score") == 3 and form and [x[0] for x in form["overall_scale"]] == [1, 2, 3, 4, 5, 6] and not str(ov.get("blocking_issue", "")).strip():
                    W("v1.5 G9: score 3 needs overall.blocking_issue (the issue that blocks acceptance, or why significance is too low); otherwise give 4")
                if not str(ov.get("g13_check", "")).strip():
                    W("v1.5 G13: overall.g13_check missing")

    # ---------------- novelty table
    for n in data.get("novelty", []) or []:
        if n.get("assessment") not in NOVELTY_LEVELS:
            E(f"novelty {n.get('contribution_id')}: invalid assessment")
        if n.get("contribution_id") not in cids:
            W(f"novelty {n.get('contribution_id')}: not among summary.contributions")
        for comp in n.get("comparisons", []) or []:
            rid = comp.get("comparator_id")
            if rid not in srcs:
                E(f"novelty {n.get('contribution_id')}: unknown comparator source {rid}")
                continue
            for j, q in enumerate(comp.get("comparator_quotes", []) or []):
                check_quote(f"novelty {n.get('contribution_id')}/{rid}[{j}]", dict(q, source_id=rid), True)
            d = parse_date(srcs[rid].get("public_date"))
            if n.get("assessment") == "overlap" and (d is None or (cutoff and d > cutoff)):
                E(f"novelty {n.get('contribution_id')}: 'overlap' relies on {rid} with unknown/late date")

    if not (data.get("policy") or {}).get("read_scope"):
        W("policy.read_scope empty: state what was actually read")

    # ---------------- neutrality (references/neutrality.md)
    pol = data.get("policy") or {}
    us = pol.get("user_stance")
    if not isinstance(us, dict) or not neutrality.kinds(data):
        (E if strict else W)("policy.user_stance not recorded: set kind (['none'] if the user expressed no stance); see references/neutrality.md")
    else:
        bad = [k for k in neutrality.kinds(data) if k not in neutrality.STANCE_KINDS]
        if bad:
            E(f"policy.user_stance.kind: invalid {bad}; use {sorted(neutrality.STANCE_KINDS)}")
        if neutrality.expressed(data) and len(str(us.get("handling", "")).strip()) < 4:
            E("policy.user_stance: a stance was expressed but 'handling' is empty")
    if data.get("overall") and mode in ("full", "self", "rebuttal"):
        sc = str((pol.get("neutrality") or {}).get("symmetry_check", "")).strip()
        if len(sc) < 15:
            (E if strict or neutrality.expressed(data) else W)(
                "policy.neutrality.symmetry_check missing: before scoring, check whether severities/score would change under the opposite expectation")
    extra = [str((us or {}).get("summary", ""))] if isinstance(us, dict) else []
    for c in data.get("comments", []) or []:
        if not isinstance(c, dict):
            continue
        hits = neutrality.scan_text(" ".join(str(c.get(k, "")) for k in ("title", "body", "request", "countercheck")), extra)
        if hits:
            E(f"{c.get('id')}: text refers to a desired outcome ('{hits[0]}'); judgments must rest on evidence only")
    ovt = data.get("overall") or {}
    hits = neutrality.scan_text(" ".join(str(ovt.get(k, "")) for k in ("justification", "would_change")), extra)
    if hits:
        E(f"overall: text refers to a desired outcome ('{hits[0]}')")
    for bf in neutrality.brief_files(base):
        hits = neutrality.scan_text(bf.read_text(encoding="utf-8", errors="replace"), extra)
        if hits:
            E(f"{bf.relative_to(base)}: sub-agent material contains the user's stance ('{hits[0]}'); regenerate it without the stance")

    # ---------------- provenance (integrity.py)
    prov = data.get("provenance")
    cur = integrity.status()
    if not cur["rules_official"]:
        W(f"guardrail rule files differ from assets/integrity.json ({', '.join(cur['changed']) or 'manifest missing'}): this review is not produced under the official rules")
    if not isinstance(prov, dict):
        W("provenance missing: review.json was not created by init_review.py of this version (add it with integrity.py stamp review.json)")
    elif prov.get("rules_sha256") != cur["rules_sha256"]:
        W(f"provenance: review was started under rules {str(prov.get('rules_sha256'))[:12]}..., current rules are {cur['rules_sha256'][:12]}...")

    # ---------------- review-of-the-review (critique.py)
    crit = data.get("critique")
    crit_types = {"misread", "already_addressed", "unsupported", "severity", "vague", "tone", "duplicate",
                  "missing_issue", "missing_literature", "inconsistent", "process", "other"}
    if isinstance(crit, dict):
        fids = set()
        for f in crit.get("findings", []) or []:
            fid = f.get("id")
            if fid in fids:
                E(f"critique {fid}: duplicate id")
            fids.add(fid)
            if f.get("type") not in crit_types:
                E(f"critique {fid}: invalid type {f.get('type')!r}")
            if f.get("resolution") not in ("accepted", "partial", "rejected"):
                E(f"critique {fid}: unresolved - set resolution (accepted/partial/rejected) and response")
            elif len(str(f.get("response", "")).strip()) < 4:
                E(f"critique {fid}: response missing")
        if mode in ("full", "self") and not any(f.get("source") == "critic" for f in crit.get("findings", []) or []) \
                and not crit.get("critic"):
            W("critique: only deterministic lint, no independent critic (critique.py plan)")
    elif mode in ("full", "self"):
        (E if strict else W)("no critique: run critique.py lint and an independent critic (references/critique.md)")

    # English submission draft (references/submission.md)
    sub_path = base / "submission_en.json"
    pol = data.get("policy") or {}
    if sub_path.exists():
        import submission  # local import: submission.py imports neutrality only
        try:
            sres = submission.check(read_json(sub_path), data)
        except ValueError as e:
            sres = {"errors": [f"cannot read: {e}"], "warnings": []}
        for e in sres["errors"]:
            E(f"submission_en.json: {e}")
        for w in sres["warnings"]:
            W(f"submission_en.json: {w}")
    elif mode in ("full", "self") and not str(pol.get("submission_skipped", "")).strip():
        (E if strict else W)("no submission_en.json: write the English submission draft (submission.py skeleton, "
                             "references/submission.md) or set policy.submission_skipped to a reason")

    if strict:
        exempt = ("prompt-injection", "not verified for this year", "declared manually", "no independent critic",
                  "review was started under rules", "words (expected", "not ordered by length", "(default ",
                  "v1.5")
        errors += [f"[strict] {w}" for w in warnings if not any(x in w for x in exempt)]
    stats = {
        "comments": len(ids),
        "by_status": {k: sum(1 for c in ids.values() if c.get("status") == k) for k in STATUSES},
        "major": sum(1 for c in ids.values() if c.get("severity") == "major" and c.get("status") != "strength"),
        "evidence_quotes": sum(len(c.get("evidence", []) or []) for c in ids.values()),
        "numeric_checks": len(nchecks),
        "sources": len(srcs),
    }
    return {"valid": not errors, "errors": errors, "warnings": warnings, "stats": stats,
            "notice": "完整性与流程检查通过不代表结论正确；每条意见的语义仍需人工复核。"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true", help="print machine-readable result")
    a = ap.parse_args()
    p = Path(a.review)
    res = validate(read_json(p), p.parent, a.strict)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(("通过" if res["valid"] else "未通过") + f"  {json.dumps(res['stats'], ensure_ascii=False)}")
        for e in res["errors"]:
            print("  错误:", e)
        for w in res["warnings"]:
            print("  警告:", w)
        print(res["notice"])
    return 0 if res["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
