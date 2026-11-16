"""Render review.json into Chinese Markdown, standalone HTML, and a venue form.

    python3 render.py WORK/review.json [--format md,html,form] [--venue ICML] [--out-dir WORK]

Outputs review.md, review.html and form.md (the venue's review form filled from
the same data; scale labels come from assets/venues.json). One template works
for every paper - no per-paper build scripts.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import neutrality  # noqa: E402
from common import CONFIDENCES, DIMENSIONS, NOVELTY_LEVELS, SCOPES, SEVERITIES, STATUSES, read_json  # noqa: E402

DIM_STATUS = {"issues": "有问题", "ok": "已检查，无问题", "na": "不适用", "unchecked": "未检查"}
MODE = {"quick": "快速预审", "full": "完整审稿", "self": "投稿前自查", "rebuttal": "答辩评估"}
RESOLUTION = {"resolved": "已解决", "partial": "部分解决", "unresolved": "未解决"}
SEV_ORDER = {"major": 0, "minor": 1, None: 2}


def _loc(c: dict) -> str:
    if c.get("scope") == "global":
        return "全文"
    return f"第 {c.get('page')} 页" + ("（区域）" if c.get("scope") == "region" else "")


def _cell(s) -> str:
    return str(s if s is not None else "").replace("|", "／").replace("\n", " ")


def _sources(data):
    return {s["id"]: s for s in data.get("sources", [])}


def comment_md(c: dict, srcs: dict, level: str = "###") -> list[str]:
    tags = [STATUSES.get(c.get("status"), c.get("status"))]
    if c.get("status") != "strength" and c.get("severity"):
        tags.append(SEVERITIES[c["severity"]])
    if c.get("confidence"):
        tags.append("置信度" + CONFIDENCES[c["confidence"]])
    tags += [DIMENSIONS.get(c.get("dimension"), c.get("dimension")), _loc(c)]
    if c.get("resolution"):
        tags.append(RESOLUTION.get(c["resolution"], c["resolution"]))
    out = [f"{level} {c['id']} {c['title']}", "", "`" + " · ".join(t for t in tags if t) + "`", "", c.get("body", "").strip(), ""]
    for e in c.get("evidence", []) or []:
        s = srcs.get(e.get("source_id"), {})
        who = "本文" if s.get("role") == "manuscript" else (s.get("title") or e.get("source_id"))
        ver = f" {s.get('version')}" if s.get("role") != "manuscript" and s.get("version") else ""
        pg = f"，p.{e['page']}" if e.get("page") else ""
        out += [f"> “{e['quote']}”（{who}{ver}{pg}）", ""]
    if c.get("comparison"):
        out += [f"**机制比较**：{c['comparison']}", ""]
    if c.get("countercheck"):
        out += [f"**反证检查**：{c['countercheck']}", ""]
    if c.get("request"):
        out += [f"**建议作者**：{c['request']}", ""]
    return out


def to_markdown(data: dict, for_critic: bool = False) -> str:
    p, ov, pol = data.get("paper", {}), data.get("overall") or {}, data.get("policy") or {}
    srcs = _sources(data)
    cs = data.get("comments", [])
    byid = {c["id"]: c for c in cs}
    venue = data.get("venue") or {}
    L = [f"# 《{p.get('title', '')}》审稿意见", ""]
    L += ["| 项目 | 内容 |", "| --- | --- |",
          f"| 审稿版本 | {p.get('version', '')} |",
          f"| 模式 | {MODE.get(data.get('mode'), data.get('mode'))} |",
          f"| 目标会议/期刊 | {' '.join(str(x) for x in (venue.get('id'), venue.get('year'), venue.get('track')) if x) or '未指定'} |",
          f"| 论文类型 | {p.get('paper_type', '')} |",
          f"| 创新性截止日期 | {data.get('cutoff', '')} |",
          f"| 审稿日期 | {data.get('review_date', '')} |",
          f"| 盲审 | {'是（未查看他人评审、评分或讨论）' if pol.get('blind') else '否'} |",
          f"| 阅读范围 | {pol.get('read_scope', '')} |",
          f"| 未做的事 | {'；'.join(pol.get('not_done', []) or []) or '无'} |"]
    if pol.get("prompt_injection"):
        L.append(f"| 提示注入 | 发现疑似注入文字（{_cell('；'.join(pol['prompt_injection'][:3]))}），已作为数据处理，未影响判断 |")
    if not for_critic:
        us = neutrality.stance(data)
        if neutrality.expressed(data):
            kl = "、".join(neutrality.STANCE_KINDS.get(k, k) for k in neutrality.kinds(data) if k != "none")
            L.append(f"| 使用者表态 | {kl}：{us.get('summary', '')}。处理：{us.get('handling', '')} |")
        elif neutrality.kinds(data):
            L.append("| 使用者表态 | 无" + (f"（{us['handling']}）" if us.get("handling") else "") + " |")
        sc = (pol.get("neutrality") or {}).get("symmetry_check")
        if sc:
            L.append(f"| 对称自检 | {sc} |")
        prov = data.get("provenance") or {}
        if prov:
            L.append(f"| 规则版本 | v{prov.get('skill_version', '?')}，规则指纹 {str(prov.get('rules_sha256', ''))[:16]}…，"
                     + ("官方规则（与清单一致）" if prov.get("rules_official") else "**规则已修改，非官方版本**") + " |")
    L.append("")
    L[:] = [x if not x.startswith("| ") or x.startswith("| ---") or x.startswith("| 项目") else
            "| " + " | ".join(_cell(c.strip()) for c in x.strip()[2:-2].split(" | ")) + " |" for x in L]
    if ov:
        L += ["## 总评", "", f"**推荐**：{ov.get('recommendation', '')}" + (f"（{ov.get('score')} 分）" if ov.get("score") is not None else "")
              + f"　**置信度**：{ov.get('confidence', '')}/5", ""]
        if ov.get("subscores"):
            L += ["**分项**：" + "，".join(f"{k} {v}" for k, v in ov["subscores"].items()), ""]
        L += [ov.get("justification", ""), ""]
        if ov.get("decisive_ids"):
            L += ["**决定评分的意见**：" + "、".join(f"{i}（{byid[i]['title']}）" for i in ov["decisive_ids"] if i in byid), ""]
        if ov.get("would_change"):
            L += [f"**评分可能改变的条件**：{ov['would_change']}", ""]
    sm = data.get("summary") or {}
    L += ["## 论文概述", ""]
    for k, lab in (("one_line", "一句话"), ("problem", "问题"), ("method", "方法"), ("setup", "实验")):
        if sm.get(k):
            L += [f"**{lab}**：{sm[k]}", ""]
    if sm.get("contributions"):
        L += ["| 编号 | 贡献 | 位置 | 创新性判断 |", "| --- | --- | --- | --- |"]
        for c in sm["contributions"]:
            L.append(f"| {c.get('id')} | {_cell(c.get('claim', ''))} | p.{c.get('page', '')} | {NOVELTY_LEVELS.get(c.get('novelty_assessment'), '—')} |")
        L.append("")

    def section(title, items):
        nonlocal L
        if items:
            L += [f"## {title}", ""]
            for c in items:
                L += comment_md(c, srcs)

    def srt(x):
        return sorted(x, key=lambda c: (SEV_ORDER.get(c.get("severity"), 2), c["id"]))
    section("主要优点", [c for c in cs if c.get("status") == "strength"])
    crit = [c for c in cs if c.get("status") in ("confirmed", "limitation")]
    section("主要问题", srt([c for c in crit if c.get("severity") == "major"]))
    section("次要问题", srt([c for c in crit if c.get("severity") != "major"]))
    section("给作者的问题", srt([c for c in cs if c.get("status") == "question"]))
    section("改进建议", srt([c for c in cs if c.get("status") == "suggestion"]))
    if data.get("rebuttal"):
        L += ["## 答辩评估", "", "| 关切 | 审稿人 | 作者回应 | 判定 |", "| --- | --- | --- | --- |"]
        for r in data["rebuttal"]:
            L.append(f"| {r.get('concern', '')} | {r.get('reviewer', '')} | {r.get('response_summary', '')} | {RESOLUTION.get(r.get('verdict'), r.get('verdict'))} |")
        L.append("")
    wd = pol.get("withdrawn") or []
    if wd:
        L += ["## 已撤销或降级的候选批评", "", "以下候选问题经反证检查后撤销或降级，列出以便核对审稿过程：", "",
              "| 候选批评 | 处理 | 原因 |", "| --- | --- | --- |"]
        for w in wd:
            L.append(f"| {_cell(w.get('candidate'))} | {_cell(w.get('action', '撤销'))} | {_cell(w.get('reason'))} |")
        L.append("")
    crit = data.get("critique") or {}
    if crit.get("findings"):
        from critique import RESOLUTIONS, TYPES  # noqa: PLC0415
        fs = crit["findings"]
        acc = sum(1 for f in fs if f.get("resolution") == "accepted")
        part = sum(1 for f in fs if f.get("resolution") == "partial")
        L += ["## 审稿意见复核", "",
              f"复核者：{crit.get('critic') or '确定性检查（critique.py lint）'}。共 {len(fs)} 条复核意见：采纳 {acc} 条，部分采纳 {part} 条，"
              f"不采纳 {len(fs) - acc - part} 条。", ""]
        if crit.get("summary"):
            L += [f"复核结论：{crit['summary']}", ""]
        if crit.get("abstractions"):
            L += ["上位问题：" + "；".join(crit["abstractions"]), ""]
        L += ["| 编号 | 类型 | 对象 | 复核意见 | 处理 | 说明 |", "| --- | --- | --- | --- | --- | --- |"]
        for f in fs:
            L.append(f"| {f.get('id')} | {TYPES.get(f.get('type'), f.get('type'))} | {_cell(f.get('target'))} | {_cell(f.get('detail'))} | "
                     f"{RESOLUTIONS.get(f.get('resolution'), '未处理')} | {_cell(f.get('response'))} |")
        L.append("")
    dims = data.get("dimensions") or {}
    L += ["## 九维度检查", "", "| 维度 | 结论 | 关联意见 | 检查说明 |", "| --- | --- | --- | --- |"]
    for k, lab in DIMENSIONS.items():
        d = dims.get(k, {})
        L.append(f"| {lab} | {DIM_STATUS.get(d.get('status'), '—')} | {'、'.join(d.get('comment_ids', []) or []) or '—'} | {_cell(d.get('note', ''))} |")
    L.append("")
    if data.get("novelty"):
        L += ["## 逐贡献创新性比较", ""]
        for n in data["novelty"]:
            L += [f"### {n['contribution_id']}：{NOVELTY_LEVELS.get(n.get('assessment'), n.get('assessment'))}", "", n.get("rationale") or "", ""]
            for comp in n.get("comparisons") or []:
                s = srcs.get(comp.get("comparator_id"), {})
                L += [f"- **{s.get('title') or comp.get('comparator_id')}**（{s.get('version', '')}，{s.get('public_date', '日期未知')}）："
                      f"共同点：{comp.get('common_mechanism', '')}；差异：{comp.get('differences', '')}"]
            if n.get("uncertainty"):
                L += ["", f"不确定性：{n['uncertainty']}"]
            L.append("")
    if data.get("numeric_checks"):
        L += ["## 数值复算", "", "| 编号 | 说明 | 算式 | 复算结果 | 论文中的数字 |", "| --- | --- | --- | --- | --- |"]
        for n in data["numeric_checks"]:
            L.append(f"| {n.get('id')} | {_cell(n.get('claim', ''))} | `{n.get('expression')}` | {n.get('value', n.get('reported'))} | {n.get('claimed', '—')} |")
        L.append("")
    if srcs:
        L += ["## 来源与版本", "", "| 编号 | 角色 | 标题 | 版本 | 公开日期 | 日期核验 |", "| --- | --- | --- | --- | --- | --- |"]
        for s in srcs.values():
            dv = s.get("date_verified") or {}
            L.append(f"| {s['id']} | {s.get('role', '')} | {_cell(s.get('title'))} | {s.get('version') or ''} | {s.get('public_date') or '未知'} | {dv.get('method', '未核验')} |")
        L.append("")
    L += ["---", "", "说明：引文与页码经脚本核对存在于对应版本的文字快照中；脚本不判断引文是否支持结论。审稿结论来自对原文的阅读，未复现实验的部分已在“未做的事”中注明。", ""]
    return "\n".join(L)


# ----------------------------------------------------------------- tiny markdown -> html
def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def md_to_html(md: str, title: str) -> str:
    out, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#"):
            n = len(ln) - len(ln.lstrip("#"))
            text = ln[n:].strip()
            anchor = re.match(r"([A-Z]\d+|C\d+)", text)
            aid = f' id="{anchor.group(1)}"' if anchor else ""
            out.append(f"<h{n}{aid}>{_inline(text)}</h{n}>")
        elif ln.startswith("| ") and i + 1 < len(lines) and lines[i + 1].startswith("| ---"):
            head = [c.strip() for c in ln.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(h)}</th>" for h in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")
            continue
        elif ln.startswith("> "):
            out.append(f"<blockquote>{_inline(ln[2:])}</blockquote>")
        elif ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif ln.strip() == "---":
            out.append("<hr>")
        elif ln.strip():
            para = [ln]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|\| |> |- |---)", lines[i + 1]):
                i += 1
                para.append(lines[i])
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
        i += 1
    css = """
body{font-family:"Times New Roman","Songti SC","STSong","SimSun",serif;max-width:900px;margin:40px auto;padding:0 24px;line-height:1.75;color:#1f2328;font-size:16px}
h1{font-size:26px;border-bottom:2px solid #333;padding-bottom:8px}h2{font-size:21px;margin-top:36px;border-bottom:1px solid #ddd;padding-bottom:4px}
h3{font-size:17px;margin-top:26px}code{background:#f1f3f5;border-radius:4px;padding:1px 6px;font-size:13px;font-family:"Times New Roman","Songti SC",serif}
blockquote{margin:6px 0;padding:6px 14px;border-left:4px solid #8aa4c8;background:#f6f8fb;color:#333}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:14.5px}th,td{border:1px solid #d0d7de;padding:6px 8px;vertical-align:top;text-align:left}th{background:#f6f8fa}
@media print{body{margin:0;max-width:none}h2{page-break-after:avoid}blockquote,table{page-break-inside:avoid}}"""
    return (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{css}</style></head><body>{''.join(out)}</body></html>")


# ----------------------------------------------------------------- venue form
def to_form(data: dict, venue_id: str | None) -> str:
    vj = read_json(HERE.parent / "assets" / "venues.json")
    vid = venue_id or (data.get("venue") or {}).get("id")
    v = vj["venues"].get(vid) if vid else None
    form_key = v["form"] if v else "generic_conference"
    form = vj["forms"][form_key]
    srcs = _sources(data)
    cs = data.get("comments", [])
    ov = data.get("overall") or {}
    sm = data.get("summary") or {}

    def label(scale, val):
        return next((f"{x[0]} = {x[1]}" for x in scale if x[0] == val), f"{val}（不在量表中）")

    def block(items):
        res = []
        for n, c in enumerate(items, 1):
            ev = "；".join(f"“{e['quote']}”（p.{e.get('page', '?')}）" for e in (c.get("evidence") or [])[:2]
                          if srcs.get(e.get("source_id"), {}).get("role") == "manuscript")
            res.append(f"{n}. **{c['title']}**（{_loc(c)}）{c['body'].strip()}" + (f" 依据：{ev}" if ev else "")
                       + (f" 建议：{c['request']}" if c.get("request") else ""))
        return res or ["无。"]
    ethics = [c for c in cs if c.get("ethics_flag")]
    crit = sorted([c for c in cs if c.get("status") in ("confirmed", "limitation") and not c.get("ethics_flag")],
                  key=lambda c: (SEV_ORDER.get(c.get("severity"), 2), c["id"]))
    strengths = [c for c in cs if c.get("status") == "strength"]
    questions = [c for c in cs if c.get("status") == "question"]
    sugg = [c for c in cs if c.get("status") == "suggestion"]
    lim = [c for c in cs if c.get("dimension") == "limitations" and c.get("status") != "strength" and not c.get("ethics_flag")]
    summary = [sm.get("one_line", "")] + ([sm["method"]] if sm.get("method") else []) + \
              [f"{c['id']}：{c.get('claim', '')}" for c in sm.get("contributions", []) or []]
    L = [f"# 审稿表单：{vid or '通用会议'}（{form_key}）", ""]
    if not form.get("verified"):
        L += [f"> 注意：该表单为近年常见形式或通用模板，提交前请核对当年审稿指南{('：' + form['guide_url']) if form.get('guide_url') else ''}。", ""]
    else:
        L += [f"> 表单已按 {form.get('year', '')} 年官方指南核对（{form.get('checked', '')}）。", ""]
    if form.get("llm_policy"):
        L += [f"> LLM 使用政策：{form['llm_policy']}", ""]
    dims = data.get("dimensions") or {}
    for sec in form["sections"]:
        L += [f"## {sec}", ""]
        s = sec.lower()
        if "优点与不足" in sec:
            L += ["**优点**", ""] + block(strengths) + ["", "**不足**", ""] + block(crit)
        elif "概述" in sec or "summary" in s and "strength" not in s:
            L += [x for x in summary if x]
        elif "优点" in sec or "strength" in s:
            L += block(strengths)
        elif "次要问题" in sec:
            L += block([c for c in crit if c.get("severity") != "major"])
        elif "主要问题" in sec:
            L += block([c for c in crit if c.get("severity") == "major"])
        elif "不足" in sec or "weakness" in s:
            L += block(crit)
        elif "伦理" in sec or "ethic" in s:
            L += block(ethics) if ethics else ["未发现需要伦理审查的问题。"]
        elif "问题" in sec or "question" in s:
            qs = block(questions) if questions else []
            if len(questions) < 3:  # turn concrete requests of major issues into questions (forms ask for 3-5)
                extra = [c for c in crit if c.get("severity") == "major" and c.get("request")][: 3 - len(questions)]
                qs += [f"{len(questions) + i}. （对应 {c['id']}）请作者：{c['request']}" for i, c in enumerate(extra, 1)]
            L += qs or ["无。"]
        elif "局限" in sec or "limitation" in s:
            note = (dims.get("limitations") or {}).get("note", "")
            L += ([note] if note else []) + (block(lim) if lim else ([] if note else ["作者已讨论主要局限。"]))
        elif "置信度" in sec or "confidence" in s:
            L += [label(form["confidence_scale"], ov.get("confidence"))]
        elif "可复现" in sec or "reproducib" in s:
            L += [(dims.get("reproducibility") or {}).get("note", "") or "见主要问题。"]
        elif any(k in s for k in ("soundness", "excitement", "presentation", "significance", "originality", "contribution")) and \
                not any(k in sec for k in ("推荐", "总评", "评估")):
            key = next(k for k in form.get("subscores", {}) if k in s) if any(k in s for k in form.get("subscores", {})) else None
            L += [label(form["subscores"][key], (ov.get("subscores") or {}).get(key)) if key else "（见优点与不足）"]
        elif any(k in sec for k in ("理由", "Justification")):
            L += [ov.get("justification", "")] + ([f"评分可能改变的条件：{ov['would_change']}"] if ov.get("would_change") else [])
        elif any(k in sec for k in ("推荐", "总评", "评分", "评估", "Rating", "Recommendation")):
            L += [label(form["overall_scale"], ov.get("score")), "", ov.get("justification", "")]
            if form.get("subscores"):
                L += [""] + [f"- {k}：{label(sc, (ov.get('subscores') or {}).get(k))}" for k, sc in form["subscores"].items()]
        elif "修改要求" in sec or "建议" in sec or "comment" in s:
            L += block(sugg + [c for c in crit if c.get("request")])
        elif "编辑" in sec:
            L += [ov.get("justification", "")]
        else:
            L += ["（请补充）"]
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review")
    ap.add_argument("--format", default="md,html,form")
    ap.add_argument("--venue", default=None)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    rp = Path(a.review)
    data = read_json(rp)
    out = Path(a.out_dir) if a.out_dir else rp.parent
    out.mkdir(parents=True, exist_ok=True)
    fmts = set(a.format.split(","))
    md = to_markdown(data)
    if "md" in fmts:
        (out / "review.md").write_text(md, encoding="utf-8")
    if "html" in fmts:
        (out / "review.html").write_text(md_to_html(md, data.get("paper", {}).get("title", "审稿意见")), encoding="utf-8")
    if "form" in fmts:
        (out / "form.md").write_text(to_form(data, a.venue), encoding="utf-8")
    print("写出：" + "、".join(str(out / n) for n, f in (("review.md", "md"), ("review.html", "html"), ("form.md", "form")) if f in fmts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
