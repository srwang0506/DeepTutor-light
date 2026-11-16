"""Author revision closure: issue board -> plan -> verify changes in the new PDF -> response letter.

    python3 revision.py board  WORK/review.json --out WORK/revision/issues.json [--external reviews.json]
    python3 revision.py plan   WORK/revision/issues.json                 # priority order (impact x cost)
    python3 revision.py diff   WORK/sources/paper.txt WORK/sources/revised.txt --out WORK/revision/diff.json
    python3 revision.py verify WORK/revision/issues.json --old WORK/sources/paper.txt --new WORK/sources/revised.txt
    python3 revision.py letter WORK/revision/issues.json --lang en --limit 5000 --out WORK/revision/response.md
    python3 revision.py report WORK/revision/issues.json --out WORK/revision/board.md [--html]

Statuses: open -> planned -> done (claimed by the author) -> verified (script found the change) | failed;
or wontfix (with a reason). `verify` only accepts a change when the cited quote exists in the NEW
snapshot and did NOT exist in the old one (kind "added"); pointing at text that was already there is
allowed only as kind "existing" (a clarification that the paper already answered the point); a deletion
is kind "removed" (present in the old snapshot, absent from the new one).
The closure gate passes when every major issue is verified or wontfix-with-reason.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import DIMENSIONS, normalize_text, now_iso, read_json, write_json  # noqa: E402
from evidence import Snapshot  # noqa: E402

STATUS = {"open": "未处理", "planned": "已计划", "done": "作者称已完成", "verified": "已核验", "failed": "核验失败", "wontfix": "不修改"}
ACTION = {"clarify": "澄清/改写", "experiment": "补实验", "analysis": "补分析", "rewrite": "重写表述", "cite": "补引用", "fix": "修正错误", "other": "其他"}
COST = {"low": 3, "medium": 2, "high": 1}
SEV = {"major": 3, "minor": 1}


# ------------------------------------------------------------------ board
def board(review: dict, external: list | None = None) -> dict:
    issues = []
    for c in review.get("comments", []):
        if c.get("status") == "strength":
            continue
        issues.append({
            "id": f"I-{c['id']}", "origin": {"type": "review", "comment_id": c["id"], "reviewer": "本审稿"},
            "title": c.get("title", ""), "detail": c.get("body", ""), "request": c.get("request", ""),
            "severity": c.get("severity", "minor"), "dimension": c.get("dimension"), "page": c.get("page"),
            "status": "open", "plan": {"action": None, "cost": None, "location": "", "note": ""},
            "response": "", "verification": {"evidence": [], "result": None, "checked_at": None},
        })
    for i, e in enumerate(external or [], 1):
        issues.append({
            "id": e.get("id") or f"I-R{i:02d}", "origin": {"type": "external", "reviewer": e.get("reviewer", "外部审稿人")},
            "title": e.get("title") or e.get("text", "")[:40], "detail": e.get("text", ""), "request": e.get("request", ""),
            "severity": e.get("severity", "minor"), "dimension": e.get("dimension"), "page": e.get("page"),
            "status": "open", "plan": {"action": None, "cost": None, "location": "", "note": ""},
            "response": "", "verification": {"evidence": [], "result": None, "checked_at": None},
        })
    return {"board_version": 1, "paper": (review.get("paper") or {}).get("title"), "created_at": now_iso(), "issues": issues}


def priority(issue: dict) -> float:
    cost = COST.get((issue.get("plan") or {}).get("cost") or "medium", 2)
    return SEV.get(issue.get("severity"), 1) * cost


def plan_order(data: dict) -> list[dict]:
    live = [i for i in data["issues"] if i["status"] not in ("verified", "wontfix")]
    return sorted(live, key=lambda i: (-priority(i), i["id"]))


# ------------------------------------------------------------------ diff
_SENT = re.compile(r"(?<=[.!?。！？])\s+")


def _loose(s: str) -> str:
    return "".join(ch.lower() for ch in normalize_text(s) if ch.isalnum())


def sentences(snap: Snapshot) -> list[tuple[int | None, str]]:
    out = []
    for p in snap.pages or [{"page": None, "start": 0, "end": len(snap.text)}]:
        for s in _SENT.split(snap.text[p["start"]:p["end"]]):
            if len(_loose(s)) >= 25:
                out.append((p["page"], s.strip()))
    return out


def diff(old: Snapshot, new: Snapshot) -> dict:
    old_s, new_s = sentences(old), sentences(new)
    old_keys = {_loose(s) for _, s in old_s}
    new_keys = {_loose(s) for _, s in new_s}
    old_all, new_all = _loose(old.text), _loose(new.text)
    added = [{"page": p, "text": s} for p, s in new_s if _loose(s) not in old_keys and _loose(s) not in old_all]
    removed = [{"page": p, "text": s} for p, s in old_s if _loose(s) not in new_keys and _loose(s) not in new_all]
    return {"added": added, "removed": removed, "n_old": len(old_s), "n_new": len(new_s)}


# ------------------------------------------------------------------ verify
def verify(data: dict, old: Snapshot, new: Snapshot) -> list[str]:
    msgs = []
    for it in data["issues"]:
        st = it.get("status")
        v = it.setdefault("verification", {"evidence": []})
        if st == "wontfix":
            if len(str((it.get("plan") or {}).get("note", "")).strip()) < 6 and len(str(it.get("response", "")).strip()) < 6:
                msgs.append(f"{it['id']}: wontfix without a reason (plan.note or response)")
            continue
        if st not in ("done", "verified", "failed"):
            continue
        evs = v.get("evidence") or []
        if not evs:
            it["status"], v["result"] = "failed", "没有给出新版本中的修改位置（verification.evidence 为空）"
            msgs.append(f"{it['id']}: claimed done but no evidence in the new version")
            continue
        problems = []
        for e in evs:
            if e.get("kind") == "removed":  # deletion: must exist in the old version and be gone from the new one
                if old.find(e.get("quote", ""))[0] == "absent":
                    problems.append(f"原版本中找不到被删除的文字：“{e.get('quote', '')[:40]}”")
                elif new.find(e.get("quote", ""))[0] != "absent":
                    problems.append(f"声称已删除，但新版本中仍存在：“{e.get('quote', '')[:40]}”")
                continue
            kind_new, pages_new = new.find(e.get("quote", ""))
            if kind_new == "absent":
                problems.append(f"新版本中找不到：“{e.get('quote', '')[:40]}”")
                continue
            if e.get("page") and pages_new and e["page"] not in pages_new:
                problems.append(f"引文在新版本 p.{pages_new}，不在 p.{e['page']}")
            kind_old, _ = old.find(e.get("quote", ""))
            if e.get("kind", "added") == "added" and kind_old != "absent":
                problems.append(f"该文字在原版本中已存在，不是新增修改：“{e.get('quote', '')[:40]}”（若是指出原文已有说明，请标 kind=existing）")
        if problems:
            it["status"], v["result"] = "failed", "；".join(problems)
            msgs.append(f"{it['id']}: " + "；".join(problems))
        else:
            it["status"], v["result"] = "verified", "已在新版本中找到对应修改"
        v["checked_at"] = now_iso()
    return msgs


def gate(data: dict) -> tuple[bool, list[str]]:
    blocking = [i["id"] for i in data["issues"] if i.get("severity") == "major" and i["status"] not in ("verified", "wontfix")]
    return not blocking, blocking


# ------------------------------------------------------------------ letter / report
def letter(data: dict, lang: str = "en") -> dict[str, str]:
    by: dict[str, list] = {}
    for it in data["issues"]:
        by.setdefault(it["origin"].get("reviewer", "Reviewer"), []).append(it)
    out = {}
    for rev, items in by.items():
        L = [f"## Response to {rev}" if lang == "en" else f"## 对 {rev} 的回复", ""]
        if lang == "en":
            L += ["We thank the reviewer for the careful reading. Below we address each point; page numbers refer to the revised manuscript.", ""]
        else:
            L += ["感谢审稿人的意见。以下逐条回复，页码均指修改后的稿件。", ""]
        for n, it in enumerate(items, 1):
            head = it["title"] if lang != "en" or not it.get("title_en") else it["title_en"]
            L.append(f"**{'Point' if lang == 'en' else '问题'} {n} ({it['id']}): {head}**")
            L.append("")
            L.append((it.get("response") or ("[TODO: response]" if lang == "en" else "[待填写：回复]")).strip())
            ev = [e for e in (it.get("verification") or {}).get("evidence", []) if e.get("quote")]
            if ev:
                label = "Changes" if lang == "en" else "修改位置"
                L.append("")
                for e in ev:
                    if e.get("kind") == "removed":
                        L.append(f"- {'Removed' if lang == 'en' else '已删除'}: \"{e['quote']}\"")
                    else:
                        L.append(f"- {label} (p.{e.get('page', '?')}): \"{e['quote']}\"")
            if it["status"] == "wontfix" and (it.get("plan") or {}).get("note"):
                L += ["", ("Why we keep it unchanged: " if lang == "en" else "保留原状的理由：") + it["plan"]["note"]]
            L.append("")
        out[rev] = "\n".join(L)
    return out


def report(data: dict) -> str:
    ok, blocking = gate(data)
    counts = {k: sum(1 for i in data["issues"] if i["status"] == k) for k in STATUS}
    L = [f"# 修改闭环看板：{data.get('paper') or ''}", "",
         "　".join(f"{STATUS[k]} {v}" for k, v in counts.items() if v) or "（无问题）", "",
         ("**闭环检查通过**：所有主要问题都已核验或说明了不修改的理由。" if ok else
          f"**闭环未通过**：以下主要问题尚未核验：{'、'.join(blocking)}。"), "",
         "## 优先级（未关闭的问题）", "", "| 顺序 | 编号 | 问题 | 严重度 | 修改方式 | 成本 | 状态 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for n, it in enumerate(plan_order(data), 1):
        p = it.get("plan") or {}
        L.append(f"| {n} | {it['id']} | {it['title'].replace('|', '／')} | {'主要' if it.get('severity') == 'major' else '次要'} | "
                 f"{ACTION.get(p.get('action'), '—')} | {p.get('cost') or '—'} | {STATUS[it['status']]} |")
    L += ["", "## 全部问题", "", "| 编号 | 来源 | 问题 | 状态 | 核验结果 |", "| --- | --- | --- | --- | --- |"]
    for it in data["issues"]:
        res = (it.get("verification") or {}).get("result") or ("理由：" + (it.get("plan") or {}).get("note", "") if it["status"] == "wontfix" else "—")
        L.append(f"| {it['id']} | {it['origin'].get('reviewer', '')} | {it['title'].replace('|', '／')} | {STATUS[it['status']]} | {str(res).replace('|', '／')} |")
    return "\n".join(L) + "\n"


def _snap(path: str) -> Snapshot:
    p = Path(path)
    man = Path(str(p)[:-4] + ".manifest.json")
    return Snapshot(p.read_text(encoding="utf-8"), read_json(man)["pages"] if man.exists() else None)


def validate_board(data: dict) -> list[str]:
    errs = []
    ids = set()
    for it in data.get("issues", []):
        if it.get("id") in ids:
            errs.append(f"duplicate issue id {it.get('id')}")
        ids.add(it.get("id"))
        if it.get("status") not in STATUS:
            errs.append(f"{it.get('id')}: invalid status {it.get('status')!r}")
        a = (it.get("plan") or {}).get("action")
        if a is not None and a not in ACTION:
            errs.append(f"{it.get('id')}: invalid plan.action {a!r}")
        c = (it.get("plan") or {}).get("cost")
        if c is not None and c not in COST:
            errs.append(f"{it.get('id')}: invalid plan.cost {c!r}")
        if it.get("dimension") not in (None, *DIMENSIONS):
            errs.append(f"{it.get('id')}: invalid dimension")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("board"); b.add_argument("review"); b.add_argument("--out", required=True); b.add_argument("--external")  # noqa: E702
    p = sub.add_parser("plan"); p.add_argument("issues")  # noqa: E702
    d = sub.add_parser("diff"); d.add_argument("old"); d.add_argument("new"); d.add_argument("--out", required=True)  # noqa: E702
    v = sub.add_parser("verify"); v.add_argument("issues"); v.add_argument("--old", required=True); v.add_argument("--new", required=True)  # noqa: E702
    l_ = sub.add_parser("letter"); l_.add_argument("issues"); l_.add_argument("--lang", default="en", choices=["en", "zh"])  # noqa: E702
    l_.add_argument("--limit", type=int, default=0, help="character limit per reviewer response (e.g. OpenReview 5000)")
    l_.add_argument("--out", required=True)
    r = sub.add_parser("report"); r.add_argument("issues"); r.add_argument("--out", required=True); r.add_argument("--html", action="store_true")  # noqa: E702
    a = ap.parse_args()

    if a.cmd == "board":
        if Path(a.out).exists():
            print(f"{a.out} exists; edit it in place", file=sys.stderr)
            return 2
        data = board(read_json(a.review), read_json(a.external) if a.external else None)
        write_json(a.out, data)
        print(f"写出 {a.out}：{len(data['issues'])} 个问题。为每个问题填写 plan.action / plan.cost / plan.location 后运行 plan。")
        return 0
    if a.cmd == "diff":
        res = diff(_snap(a.old), _snap(a.new))
        write_json(a.out, res)
        print(f"新增 {len(res['added'])} 句，删除 {len(res['removed'])} 句 -> {a.out}")
        return 0
    data = read_json(a.issues)
    errs = validate_board(data)
    if errs:
        for e in errs:
            print("错误:", e)
        return 1
    if a.cmd == "plan":
        for n, it in enumerate(plan_order(data), 1):
            pl = it.get("plan") or {}
            print(f"{n:>2}. {it['id']:<8} 优先级 {priority(it):.0f}  [{'主要' if it.get('severity') == 'major' else '次要'}·"
                  f"{ACTION.get(pl.get('action'), '未定')}·成本 {pl.get('cost') or '未定'}] {it['title']}")
        return 0
    if a.cmd == "verify":
        msgs = verify(data, _snap(a.old), _snap(a.new))
        write_json(a.issues, data)
        ok, blocking = gate(data)
        for m in msgs:
            print("未通过:", m)
        print("闭环检查通过" if ok else f"闭环未通过：主要问题 {'、'.join(blocking)} 尚未核验")
        return 0 if ok else 1
    if a.cmd == "letter":
        parts = letter(data, a.lang)
        text = "\n".join(parts.values())
        Path(a.out).write_text(text, encoding="utf-8")
        for rev, t in parts.items():
            n = len(t)
            flag = f"  超出上限 {a.limit}！" if a.limit and n > a.limit else ""
            print(f"{rev}: {n} 字符{flag}")
        todo = text.count("[TODO") + text.count("[待填写")
        if todo:
            print(f"注意：还有 {todo} 处回复未填写。")
        return 1 if (a.limit and any(len(t) > a.limit for t in parts.values())) else 0
    md = report(data)
    Path(a.out).write_text(md, encoding="utf-8")
    if a.html:
        from render import md_to_html  # noqa: PLC0415
        Path(a.out).with_suffix(".html").write_text(md_to_html(md, "修改闭环看板"), encoding="utf-8")
    print(f"写出 {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
