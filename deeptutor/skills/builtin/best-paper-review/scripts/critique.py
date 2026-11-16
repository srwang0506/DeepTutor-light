"""Review-of-the-review: deterministic lint, independent critic brief, merge.

    python3 critique.py lint  WORK/review.json [--dry-run]
    python3 critique.py plan  WORK/review.json --out WORK/critique
    python3 critique.py merge WORK/critique/critique.json --review WORK/review.json

`lint` runs deterministic checks on the review itself (duplicates, score vs.
decisive issues, low-confidence confirmations, uncomputed numbers, missing
requests, tone, missing upper-level literature search, ...) and stores the
findings in review["critique"]. `plan` writes a self-contained brief for an
independent critic sub-agent. `merge` validates the critic's quotes and adds
its findings. Every finding must then get `resolution` + `response` from the
main reviewer; evidence.py --strict refuses unresolved findings.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import DIMENSIONS, now_iso, read_json, write_json  # noqa: E402
from evidence import Snapshot  # noqa: E402
import neutrality  # noqa: E402

TYPES = {
    "misread": "误读原文", "already_addressed": "论文已回应", "unsupported": "证据不足", "severity": "轻重不当",
    "vague": "表述空泛", "tone": "语气不当", "duplicate": "意见重复", "missing_issue": "遗漏问题",
    "missing_literature": "遗漏文献线", "inconsistent": "总评与意见不一致", "process": "流程缺口", "other": "其他",
}
RESOLUTIONS = {"accepted": "采纳", "partial": "部分采纳", "rejected": "不采纳"}
HARSH = ["显然", "毫无", "荒谬", "可笑", "根本不懂", "完全错误", "毫无意义", "obviously", "trivial", "nonsense", "clearly wrong"]
NUM_RE = re.compile(r"(?:降低|提升|提高|降幅|增幅|相差|差距|高出|低了|差了|改进)[^。；\n]{0,14}?\d+(?:\.\d+)?\s*(?:%|个百分点|倍)")


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _jaccard(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _scale(review: dict) -> list | None:
    try:
        vj = read_json(HERE.parent / "assets" / "venues.json")
        vid = (review.get("venue") or {}).get("id")
        v = vj["venues"].get(vid) if vid else None
        return [x[0] for x in vj["forms"][v["form"] if v else "generic_conference"]["overall_scale"]]
    except (OSError, KeyError):
        return None


def lint(review: dict) -> list[dict]:
    """Deterministic checks on the review itself. Returns findings (source=lint)."""
    out: list[dict] = []

    def add(ftype, target, severity, detail, suggestion):
        out.append({"id": f"L{len(out) + 1:02d}", "source": "lint", "type": ftype, "target": target,
                    "severity": severity, "detail": detail, "suggestion": suggestion, "evidence": []})

    cs = [c for c in review.get("comments", []) if isinstance(c, dict)]
    crit = [c for c in cs if c.get("status") != "strength"]
    # 1 duplicates
    for i, a in enumerate(crit):
        for b in crit[i + 1:]:
            sim = _jaccard(a.get("title", "") + a.get("body", "")[:200], b.get("title", "") + b.get("body", "")[:200])
            if sim >= 0.45:
                add("duplicate", f"{a['id']},{b['id']}", "minor", f"{a['id']} 与 {b['id']} 的标题与开头高度相似（相似度 {sim:.2f}）。",
                    "确认是否在说同一件事；若是，合并为一条。")
    # 2 score vs decisive issues
    ov = review.get("overall") or {}
    scale = _scale(review)
    majors = [c for c in crit if c.get("severity") == "major" and c.get("status") == "confirmed"]
    if scale and ov.get("score") in scale:
        pos = scale.index(ov["score"]) / max(1, len(scale) - 1)
        if pos >= 0.6 and len(majors) >= 2:
            add("inconsistent", "overall", "major", f"总分处于量表上部，但有 {len(majors)} 条已确认的主要问题（{', '.join(c['id'] for c in majors)}）。",
                "说明这些问题为何不影响接收判断，或下调评分。")
        if pos <= 0.4 and not [c for c in crit if c.get("severity") == "major"]:
            add("inconsistent", "overall", "major", "总分处于量表下部，但没有任何主要（major）意见。", "补充决定评分的主要问题，或上调评分。")
    # 3 low-confidence confirmations
    for c in crit:
        if c.get("status") == "confirmed" and c.get("confidence") == "low":
            add("severity", c["id"], "minor", f"{c['id']} 标为确认问题，但置信度为低。", "置信度低时通常应改为“澄清问题”或“证据局限”。")
    # 4 numbers without recomputation
    for c in crit:
        if c.get("status") in ("confirmed", "limitation") and len(NUM_RE.findall(c.get("body", ""))) >= 1 and not c.get("numeric_check_ids"):
            add("unsupported", c["id"], "minor", f"{c['id']} 的论证依赖数字（如“{NUM_RE.search(c['body']).group(0)}”），但没有关联数值复算。",
                "把计算写进 numeric_checks 并关联到该意见。")
    # 5 major without concrete request
    for c in crit:
        if c.get("severity") == "major" and c.get("status") in ("confirmed", "limitation") and len(str(c.get("request", "")).strip()) < 10:
            add("vague", c["id"], "minor", f"{c['id']} 是主要问题，但没有具体的修改请求。", "写出作者可以执行的请求（补什么实验、改哪句话）。")
    # 6 tone
    for c in cs:
        hit = [w for w in HARSH if w in (c.get("title", "") + c.get("body", "")).lower()]
        if hit:
            add("tone", c["id"], "minor", f"{c['id']} 含有可能不当的措辞：{'、'.join(hit)}。", "改为对事不对人的描述。")
    # 7 fairness: no strengths
    if not [c for c in cs if c.get("status") == "strength"] and review.get("mode") != "quick":
        add("inconsistent", "overall", "minor", "审稿中没有任何优点。", "检查论文是否确实没有可肯定之处；有证据的优点应如实写出。")
    # 8 counterfactual discipline
    if review.get("mode") in ("full", "self") and not (review.get("policy") or {}).get("withdrawn"):
        add("process", "overall", "minor", "没有记录任何撤销或降级的候选批评。", "回顾反证检查过程，把撤销的候选记入 policy.withdrawn；若确实没有，在回应中说明。")
    # 9 upper-level literature search
    if review.get("novelty") and not (review.get("critique") or {}).get("abstractions"):
        kinds = {s.get("kind") for n in review["novelty"] for s in (n.get("searches") or []) if isinstance(s, dict)}
        if "abstraction" not in kinds:
            add("missing_literature", "novelty", "major", "创新性检索中没有“上位问题”检索：只按方法关键词检索，容易漏掉解决同一经典问题的文献线。",
                "把方法抽象为它要解决的经典问题（例如“让机器人从人类示范中学会操作”→ 模仿学习/行为克隆、逆强化学习；“把低分辨率视频变清晰”→ 视频超分辨率、盲图像复原），补检这一路线并在审稿中讨论。")
    # 11 neutrality: stance recorded, symmetry check before scoring
    if not neutrality.kinds(review):
        add("process", "overall", "minor", "没有记录使用者表态（policy.user_stance）。", "按 references/neutrality.md 记录；没有表态时写 kind: [\"none\"]。")
    if ov and review.get("mode") in ("full", "self", "rebuttal") and \
            len(str(((review.get("policy") or {}).get("neutrality") or {}).get("symmetry_check", "")).strip()) < 15:
        add("process", "overall", "major" if neutrality.expressed(review) else "minor",
            "出分前没有做对称自检（policy.neutrality.symmetry_check）。",
            "把期望反过来，逐条检查 major 意见的定级与总分是否会变，写出结论。")
    if "selective" in neutrality.kinds(review) and not [c for c in cs if c.get("status") == "strength"]:
        add("inconsistent", "overall", "major", "使用者要求只写缺点，而审稿中确实没有优点。", "选择性要求不执行：按证据补写优点。")
    # 10 dimensions marked ok while comments criticise them
    dims = review.get("dimensions") or {}
    for k in DIMENSIONS:
        d = dims.get(k) or {}
        if d.get("status") == "ok" and any(c.get("dimension") == k and c.get("severity") == "major" for c in crit):
            add("inconsistent", k, "minor", f"维度“{DIMENSIONS[k]}”标为无问题，但有主要意见属于该维度。", "统一维度结论与意见。")
    return out


BRIEF = """# 审稿意见复核任务

你是一名**独立复核者**。你的任务不是再审一遍论文找更多缺点，而是审查下面这份**审稿意见本身**是否读对了原文、是否公平、轻重是否得当、是否漏掉了重要问题或整条文献线。你的结论会交给主审稿人逐条回应。

## 材料
- 论文 PDF：`{pdf}`（看图表和公式时用 Read 工具按页读取）
- 论文文字快照：`{snapshot}`（带页码；用 `python3 {scripts}/find_quote.py {snapshot} --grep "关键词"` 搜索，用 `python3 {scripts}/find_quote.py {snapshot} "逐字引文"` 核对引文并取得页码）
- 待复核的审稿意见：`{review_md}`
- 目标会议：{venue}；创新性截止日期：{cutoff}；论文类型：{paper_type}

## 约束
- 只依据论文原文与截止日期前的公开文献。**不得检索、打开或引用这篇论文的公开评审、讨论、评分或作者回复**，检索时不要使用论文标题。
- 你看不到主审稿人的思考过程，这是有意的；请从原文重新理解论文。
- 不为凑数提意见。没有问题的地方不必写。

## 步骤
1. 先通读论文（含附录），再读审稿意见。
2. 逐条核对每条意见（编号 S/W/Q/G…）：重读它引用的页面与上下文，判断是否误读、论文是否已回应、证据是否足够、轻重是否得当、表述是否具体、是否与其他意见重复。
3. 独立按九个维度（问题与动机、创新性、方法正确性、理论、实验设计、基线公平性、可复现性、写作与图表、局限与伦理）快速再查一遍，记录有原文证据、但审稿意见没有提到的问题。
4. **上位问题检索**：把方法抽象成它要解决的经典问题，写出 2–3 个上位问题表述，检查审稿是否讨论过这些文献路线。需要时检索：
   `python3 {scripts}/sources.py arxiv "<上位问题关键词>" --before {cutoff} --out {work}/search-<n>.json`
   （也可用 openalex、semantic、dblp；数据源报错时换一个，并在 summary 中说明。）
5. 若论文含修订标记（如红色文字、“revised”），检查新增内容是否真的回答了它要回应的问题、是否与表格一致。
6. 检查总评与意见是否一致，是否公平对待了论文的优点。
7. 写出结果文件 `{out_file}`（格式见下），然后用三四句中文向主审稿人汇报最重要的发现。

## 输出格式（JSON）
```json
{{
  "critic": "独立复核子代理（新上下文）",
  "date": "{today}",
  "abstractions": ["上位问题表述 1", "上位问题表述 2"],
  "findings": [
    {{"id": "K01", "type": "misread", "target": "W05", "severity": "major",
      "detail": "具体说明", "evidence": [{{"source_id": "paper", "quote": "逐字引文", "page": 21}}],
      "suggestion": "如何修改"}}
  ],
  "summary": "总体判断与检索覆盖说明"
}}
```
`type` 取值：misread（误读原文）、already_addressed（论文已回应）、unsupported（证据不足）、severity（轻重不当）、vague（表述空泛）、tone（语气不当）、duplicate（意见重复）、missing_issue（遗漏问题）、missing_literature（遗漏文献线）、inconsistent（总评与意见不一致）、other。
`target` 取值：意见编号、`overall`、`novelty` 或 `missing`。`evidence` 只能引用论文快照（source_id 为 `paper`），必须逐字复制并写对页码；文献线类发现在 `detail` 中写出代表性文献的标题、年份与 arXiv 编号即可。
特别检查**偏向**：若某些意见的严重度明显高于（或低于）证据所能支持的程度、有证据的优点被省略、或反证检查流于形式，用 `severity` 或 `inconsistent` 类型指出，不论偏向哪一方。
""" + neutrality.BRIEF_NOTE


def plan(review_path: Path, out: Path) -> Path:
    review = read_json(review_path)
    base = review_path.parent
    out.mkdir(parents=True, exist_ok=True)
    man = next((s for s in review.get("sources", []) if s.get("role") == "manuscript"), {})
    import render  # noqa: PLC0415
    review_md = out / "review.for-critic.md"   # rendered without the user's stance or the symmetry check
    md = render.to_markdown(review, for_critic=True)
    hits = neutrality.check_brief(md, review)
    if hits:
        raise SystemExit(f"审稿正文中出现使用者的表态（“{hits[0]}”），先删去再生成复核简报。")
    review_md.write_text(md, encoding="utf-8")
    venue = review.get("venue") or {}
    brief = BRIEF.format(pdf=(base / (review.get("paper") or {}).get("pdf", "paper.pdf")).resolve(),
                         snapshot=(base / man.get("path", "sources/paper.txt")).resolve(), scripts=HERE,
                         review_md=review_md.resolve(), venue=" ".join(str(x) for x in (venue.get("id"), venue.get("year")) if x) or "未指定",
                         cutoff=review.get("cutoff"), paper_type=(review.get("paper") or {}).get("paper_type", ""),
                         work=out.resolve(), out_file=(out / "critique.json").resolve(), today=dt.date.today().isoformat())
    hits = neutrality.check_brief(brief, review)
    if hits:
        raise SystemExit(f"复核简报中出现使用者的表态（“{hits[0]}”）。")
    p = out / "critic.brief.md"
    p.write_text(brief, encoding="utf-8")
    return p


def _store(review: dict, findings: list[dict], critic: str | None = None, summary: str | None = None, abstractions=None) -> None:
    crit = review.setdefault("critique", {"findings": []})
    old = {(f.get("source"), f.get("type"), f.get("target"), f.get("detail")): f for f in crit.get("findings", [])}
    kept = [f for f in crit.get("findings", []) if f.get("source") != (findings[0]["source"] if findings else None)]
    for f in findings:
        prev = old.get((f.get("source"), f.get("type"), f.get("target"), f.get("detail")))
        if prev:  # keep the reviewer's earlier resolution when re-running
            f["resolution"], f["response"] = prev.get("resolution"), prev.get("response")
        f.setdefault("resolution", None)
        f.setdefault("response", "")
    crit["findings"] = kept + findings
    if critic:
        crit["critic"] = critic
    if summary:
        crit["summary"] = summary
    if abstractions:
        crit["abstractions"] = abstractions
    crit["updated_at"] = now_iso()


def merge(critique_path: Path, review_path: Path) -> list[str]:
    errors = []
    review = read_json(review_path)
    data = read_json(critique_path)
    base = review_path.parent
    srcs = {s["id"]: s for s in review.get("sources", [])}
    ids = {c["id"] for c in review.get("comments", [])}
    snaps: dict[str, Snapshot] = {}
    findings = []
    for i, f in enumerate(data.get("findings", []), 1):
        fid = f.get("id") or f"K{i:02d}"
        if f.get("type") not in TYPES:
            errors.append(f"{fid}: invalid type {f.get('type')!r}")
        tgt = str(f.get("target", ""))
        if tgt not in ("overall", "novelty", "missing") and not all(t.strip() in ids or t.strip() in DIMENSIONS for t in tgt.split(",")):
            errors.append(f"{fid}: unknown target {tgt!r}")
        for e in f.get("evidence", []) or []:
            sid = e.get("source_id", "paper")
            s = srcs.get(sid)
            if not s:
                errors.append(f"{fid}: unknown source {sid}")
                continue
            if sid not in snaps:
                man = read_json(base / s["manifest"]) if s.get("manifest") else {}
                snaps[sid] = Snapshot((base / s["path"]).read_text(encoding="utf-8"), man.get("pages"))
            kind, pages = snaps[sid].find(e.get("quote", ""))
            if kind == "absent":
                errors.append(f"{fid}: quote not in {sid}: '{e.get('quote', '')[:50]}'")
            elif e.get("page") and pages and e["page"] not in pages:
                errors.append(f"{fid}: quote on p.{pages}, not p.{e['page']}")
        findings.append(dict(f, id=fid.replace("L", "K", 1) if fid.startswith("L") else fid, source="critic"))
    if not errors:
        _store(review, findings, data.get("critic") or "独立复核子代理", data.get("summary"), data.get("abstractions"))
        write_json(review_path, review)
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("lint")
    lp.add_argument("review")
    lp.add_argument("--dry-run", action="store_true")
    pp = sub.add_parser("plan")
    pp.add_argument("review")
    pp.add_argument("--out", required=True)
    mp = sub.add_parser("merge")
    mp.add_argument("critique")
    mp.add_argument("--review", required=True)
    a = ap.parse_args()
    if a.cmd == "lint":
        rp = Path(a.review)
        review = read_json(rp)
        fs = lint(review)
        for f in fs:
            print(f"{f['id']} [{TYPES[f['type']]}·{f['severity']}] {f['target']}: {f['detail']}")
        print(f"共 {len(fs)} 条确定性复核意见。" + ("" if a.dry_run else " 已写入 review.json 的 critique.findings，请逐条填写 resolution 与 response。"))
        if not a.dry_run:
            _store(review, fs)
            write_json(rp, review)
        return 0
    if a.cmd == "plan":
        p = plan(Path(a.review), Path(a.out))
        print(p)
        print("-> 启动一个复核子代理（新上下文），提示词为该简报全文；完成后运行 critique.py merge。")
        return 0
    errors = merge(Path(a.critique), Path(a.review))
    for e in errors:
        print("错误:", e)
    print("合并完成，请逐条填写 resolution 与 response。" if not errors else "合并未写入：请先修复错误")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
