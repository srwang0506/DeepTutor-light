"""Plan and merge per-contribution novelty searches run by parallel sub-agents.

    python3 novelty.py plan  WORK/contributions.json --out WORK/novelty [--budget 12]
    python3 novelty.py merge WORK/novelty --review WORK/review.json

`plan` writes one self-contained brief per contribution (C1.brief.md, ...)
that is passed verbatim as the prompt of one sub-agent; launch all of them in
the same turn. `merge` validates each C*.json (quotes/pages in snapshots,
version dates vs. cutoff), merges comparator sources into review.json,
writes review["novelty"] and fills summary.contributions[].novelty_assessment.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import NOVELTY_LEVELS, now_iso, parse_date, read_json, sha256_bytes, write_json  # noqa: E402
from evidence import Snapshot  # noqa: E402
import neutrality  # noqa: E402

BRIEF = """# 创新性检索任务：{cid}

你是一名检索与比较子代理，只负责贡献 **{cid}** 的前作检索与机制比较。你的结论会被主审稿人复核，它是线索而不是最终判断。

## 贡献
- 论文：{title}
- 贡献概括：{claim}
- 机制要点（公开术语）：{mechanism}
- 建议关键词：{keywords}
- 上位问题（方法要解决的经典问题）：{abstraction}
- 论文已引用的相关工作：{known_refs}
- 创新性截止日期：**{cutoff}**（只比较此日期前公开的版本）
{confidential_note}
## 工具（技能目录：`{skill_dir}`）
```bash
S={skill_dir}/scripts
python3 $S/sources.py arxiv "<query>" --before {cutoff} --out {work}/search/{cid}-arxiv-1.json
python3 $S/sources.py openalex "<query>" --before {cutoff} --out {work}/search/{cid}-openalex-1.json
python3 $S/sources.py semantic "<query>" --before {cutoff} --out {work}/search/{cid}-s2-1.json
python3 $S/sources.py dblp "<query>" --out {work}/search/{cid}-dblp-1.json
python3 $S/sources.py semantic-refs arXiv:<id> --out {work}/search/{cid}-refs.json     # 引用追溯
python3 $S/sources.py semantic-cites arXiv:<id> --out {work}/search/{cid}-cites.json
python3 $S/sources.py merge {work}/search/{cid}-*.json --before {cutoff} --out {work}/search/{cid}-candidates.json
python3 $S/fetch_arxiv.py <arxiv_id> --cutoff {cutoff} --out {sources_dir} --rel-to {review_dir}
python3 $S/find_quote.py {sources_dir}/<id>.txt --grep "keyword"        # 在快照中搜索，显示页码与上下文
python3 $S/find_quote.py {sources_dir}/<id>.txt "逐字引文"               # 核对引文并给出页码
```
也可以使用宿主的网页检索工具。某个数据源报错（如 429/403）时，换用其他数据源，并在 `uncertainty` 中记录；报错不代表没有前作。

## 步骤（预算：最多 {budget} 次检索请求，最多下载 3 篇全文）
1. 至少四类查询，每类至少两个数据源：任务术语 / 机制术语 / 替代说法 / **上位问题**。上位问题指把方法抽象成它要解决的经典问题（例如“让机器人从人类示范中学会操作”→ 模仿学习/行为克隆、逆强化学习；“把低分辨率视频变清晰”→ 视频超分辨率、盲图像复原），专门用来发现不用相同术语、但解决同一问题的经典路线；这类前作最容易漏掉。
2. 对论文已引用的最相关工作做引用追溯。
3. 合并去重，挑出不超过 5 篇候选，排除截止日期之后的版本。
4. 对最接近的 1–3 篇，用 `fetch_arxiv.py --cutoff {cutoff}` 下载截止前的最新版本，阅读方法部分（快照文本在 `{sources_dir}/<id>.txt`）。
5. 逐篇写比较：共同机制、实际差异、问题设定、假设、保证或洞见、资源（数据/算力）、差异的意义、最有力的反方观点。
6. 引文必须逐字复制自快照文本，并写出页码（快照清单 `<id>.manifest.json` 中有每页偏移）。
7. 写出结果文件 `{out_file}`，格式见下。只写你读到的内容；没读全文的候选 `read: false`。

## 评估等级
`distinct` 有明确区别 / `incremental` 增量但有用 / `overlap` 实质重叠（必须有双方原文支持，且在相同假设下等价）/ `unresolved` 无法判定。
同名不等于同机制；组合可以是贡献；“方法简单”“提升小”不等于没有贡献；截止日期前 3 个月内公开的工作标为“同期”。

## 输出格式（JSON）
```json
{{
  "contribution_id": "{cid}",
  "searches": [{{"kind": "task|mechanism|synonym|abstraction|citation", "provider": "arxiv", "query": "...", "file": "search/{cid}-arxiv-1.json", "hits": 10, "error": null}}],
  "candidates": [{{"title": "...", "arxiv_id": "...", "version": "v1", "public_date": "YYYY-MM-DD", "read": true, "source_id": "...", "relevance": "high", "concurrent": false}}],
  "sources": [ /* fetch_arxiv.py 打印的 source 条目，原样粘贴 */ ],
  "comparisons": [{{
    "comparator_id": "<source id>",
    "common_mechanism": "...", "differences": "...", "problem": "...", "assumptions": "...",
    "guarantee_or_insight": "...", "resources": "...", "manuscript_side": "...",
    "comparator_quotes": [{{"quote": "逐字引文", "page": 3}}],
    "delta_significance": "...", "strongest_counterargument": "..."
  }}],
  "assessment": "distinct|incremental|overlap|unresolved",
  "rationale": "中文，三到六句",
  "uncertainty": "...",
  "coverage": "检索覆盖了什么、没覆盖什么"
}}
```
最后用两三句中文回复主代理：结论、最接近的前作、主要不确定性。
""" + neutrality.BRIEF_NOTE


def plan(contrib_path: Path, out: Path, budget: int, skill_dir: Path, review_dir: Path | None) -> list[Path]:
    spec = read_json(contrib_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / "search").mkdir(exist_ok=True)
    review_dir = (review_dir or contrib_path.parent).resolve()
    sources_dir = review_dir / "sources"
    conf = bool(spec.get("confidential"))
    note = ("\n> **保密稿件**：不要把稿件原文或未公开细节发送给任何检索服务；只使用上面的公开术语。\n" if conf else "")
    briefs = []
    tasks = [c for c in spec["contributions"] if c.get("search", True)]
    rv = review_dir / "review.json"
    review = read_json(rv) if rv.exists() else None
    for c in tasks:
        cid = c["id"]
        path = out / f"{cid}.brief.md"
        text = (BRIEF.format(
            cid=cid, title=spec.get("paper_title", ""), claim=c.get("claim", ""), mechanism=c.get("mechanism", ""),
            keywords=", ".join(c.get("keywords", [])) or "（自行拟定）",
            abstraction=c.get("abstraction") or "（请自行拟定 1–2 个上位问题表述）", known_refs=", ".join(c.get("known_refs", [])) or "无",
            cutoff=spec["cutoff"], confidential_note=note, skill_dir=skill_dir, work=out.resolve(),
            sources_dir=sources_dir, review_dir=review_dir, budget=budget, out_file=(out / f"{cid}.json").resolve()))
        hits = neutrality.check_brief(text, review)
        if hits:
            raise SystemExit(f"{cid}: 简报中出现使用者的表态（“{hits[0]}”）。从 contributions.json 中删去后重新生成；子代理不应知道任何人对结果的期望。")
        path.write_text(text, encoding="utf-8")
        briefs.append(path)
    write_json(out / "plan.json", {"created_at": now_iso(), "cutoff": spec["cutoff"], "confidential": conf,
                                   "tasks": [{"id": c["id"], "brief": f"{c['id']}.brief.md", "result": f"{c['id']}.json"}
                                             for c in tasks],
                                   "skipped": [c["id"] for c in spec["contributions"] if not c.get("search", True)],
                                   "instruction": "为每个任务启动一个子代理（同一轮并行），提示词为 brief 全文。"})
    return briefs


def merge(novelty_dir: Path, review_path: Path) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if not review_path.exists():
        return [f"{review_path} not found: run init_review.py first (step 1)"], []
    review = read_json(review_path)
    base = review_path.parent
    cutoff = parse_date(review.get("cutoff"))
    srcs = {s["id"]: s for s in review.get("sources", [])}
    entries = []
    plan_file = novelty_dir / "plan.json"
    expected = [t["id"] for t in read_json(plan_file)["tasks"]] if plan_file.exists() else None
    files = sorted(p for p in novelty_dir.glob("C*.json"))
    found = {p.stem for p in files}
    if expected:
        for cid in expected:
            if cid not in found:
                errors.append(f"{cid}: result file missing (sub-agent not finished or failed)")
    for f in files:
        d = read_json(f)
        cid = d.get("contribution_id", f.stem)
        if d.get("assessment") not in NOVELTY_LEVELS:
            errors.append(f"{cid}: invalid assessment {d.get('assessment')!r}")
        for s in d.get("sources", []) or []:
            s = dict(s, role=s.get("role", "comparator"))
            try:  # the snapshot file is the ground truth; parallel agents may re-describe the same download
                actual = sha256_bytes((base / s["path"]).read_bytes())
            except (OSError, KeyError):
                errors.append(f"{cid}: snapshot for source {s.get('id')} not found at {s.get('path')}")
                continue
            if s.get("sha256") and s["sha256"] != actual:
                errors.append(f"{cid}: source {s['id']} hash does not match its snapshot file")
                continue
            s["sha256"] = actual
            old = srcs.get(s["id"])
            if old and old.get("sha256") != actual:
                errors.append(f"{cid}: source id {s['id']} conflicts with an existing snapshot")
                continue
            if old is None or (not old.get("date_verified") and s.get("date_verified")):
                srcs[s["id"]] = s
        for comp in d.get("comparisons", []) or []:
            rid = comp.get("comparator_id")
            s = srcs.get(rid)
            if not s:
                errors.append(f"{cid}: comparator {rid} has no source entry")
                continue
            try:
                raw = (base / s["path"]).read_bytes()
                man = read_json(base / s["manifest"]) if s.get("manifest") else {}
            except (OSError, KeyError):
                errors.append(f"{cid}: snapshot for {rid} not readable from the review directory")
                continue
            if sha256_bytes(raw) != s.get("sha256"):
                errors.append(f"{cid}: snapshot hash mismatch for {rid}")
            snap = Snapshot(raw.decode("utf-8", errors="replace"), man.get("pages"))
            for q in comp.get("comparator_quotes", []) or []:
                kind, pages = snap.find(q.get("quote", ""))
                if kind == "absent":
                    errors.append(f"{cid}/{rid}: quote not in snapshot: '{q.get('quote', '')[:50]}'")
                elif q.get("page") and pages and q["page"] not in pages:
                    errors.append(f"{cid}/{rid}: quote on p.{pages}, not p.{q['page']}")
            pd = parse_date(s.get("public_date"))
            if pd is None:
                warnings.append(f"{cid}/{rid}: unknown public date -> cannot support overlap")
            elif cutoff and pd > cutoff:
                errors.append(f"{cid}/{rid}: version {pd} is after the cutoff {cutoff}")
            if not s.get("date_verified"):
                warnings.append(f"{cid}/{rid}: date not verified (fetch_arxiv.py or verify_dates.py)")
        if d.get("assessment") == "overlap" and not d.get("comparisons"):
            errors.append(f"{cid}: overlap without comparisons")
        entries.append({k: d.get(k) for k in ("contribution_id", "assessment", "rationale", "candidates", "comparisons",
                                              "searches", "uncertainty", "coverage")})
        entries[-1]["contribution_id"] = cid
    # contradictory descriptions of the same comparator across agents
    seen: dict[str, list[str]] = {}
    for e in entries:
        for comp in e.get("comparisons") or []:
            seen.setdefault(comp.get("comparator_id"), []).append(e["contribution_id"])
    for rid, cs in seen.items():
        if len(cs) > 1:
            warnings.append(f"comparator {rid} used by {cs}: main reviewer should check the descriptions agree")
    review["sources"] = list(srcs.values())
    review["novelty"] = entries
    skipped = read_json(plan_file).get("skipped", []) if plan_file.exists() else []
    for c in (review.get("summary") or {}).get("contributions", []) or []:
        e = next((x for x in entries if x["contribution_id"] == c.get("id")), None)
        if e:
            c["novelty_assessment"] = e["assessment"]
        elif c.get("id") in skipped:
            c["novelty_assessment"] = "na"
    if not errors:
        write_json(review_path, review)
        write_json(base / "novelty-comparisons.json", {"cutoff": review.get("cutoff"), "merged_at": now_iso(), "entries": entries})
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("contributions")
    p.add_argument("--out", required=True)
    p.add_argument("--budget", type=int, default=12)
    p.add_argument("--review-dir", default=None, help="directory of review.json (default: contributions.json's dir)")
    m = sub.add_parser("merge")
    m.add_argument("novelty_dir")
    m.add_argument("--review", required=True)
    a = ap.parse_args()
    if a.cmd == "plan":
        briefs = plan(Path(a.contributions), Path(a.out), a.budget, HERE.parent, Path(a.review_dir) if a.review_dir else None)
        print("\n".join(str(b) for b in briefs))
        print(f"-> 为以上 {len(briefs)} 个贡献各启动一个子代理（同一轮并行），提示词为对应 brief 的全文。")
        return 0
    errors, warnings = merge(Path(a.novelty_dir), Path(a.review))
    for w in warnings:
        print("警告:", w)
    for e in errors:
        print("错误:", e)
    print("合并完成" if not errors else "合并未写入：请先修复错误")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
