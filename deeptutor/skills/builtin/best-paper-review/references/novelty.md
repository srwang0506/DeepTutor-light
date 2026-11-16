# 逐贡献创新性比较与多代理并行检索

## 原则
- 对贡献逐条比较，不对全文笼统下结论。
- 先读原文再引用；检索结果的摘要只用于发现候选论文，不能作为比较依据。
- 只比较截止日期前公开的**具体版本**。后续版本新增的内容不能证明更早已公开；日期未知时结论只能是 `unresolved`。
- 同名不等于同机制，组合创新也可以是贡献；小提升、方法简单、组件已知，都不等于没有贡献。
- 有界检索不能证明“没有前作”。检索失败只说明覆盖范围未知。
- 并行子代理是分工检索，不是独立审稿人；它们的一致不构成独立证据。

## 评估等级
`distinct` 有明确区别的贡献 / `incremental` 增量但有用 / `overlap` 与前作实质重叠（需证据）/ `unresolved` 无法判定。

## 流程

### 1. 主代理：拆贡献
写 `WORK/contributions.json`：
```json
{"paper_title": "...", "cutoff": "2025-03-14", "confidential": false,
 "contributions": [{"id": "C1", "claim": "中文概括", "mechanism": "用公开术语描述的机制要点",
   "keywords": ["alternating attention", "multi-view feed-forward"], "known_refs": ["2312.14132"],
   "abstraction": "多视图几何 / 从无标定图像集合估计相机与结构"}]}
```
不需要前作检索的贡献（纯实证结果、评测）加 `"search": false`，合并后记为 `na`。
`confidential: true`（未公开稿件）时，`mechanism` 和 `keywords` 只能使用公开概念，不得粘贴稿件原文。

### 用户提供的前作
不在 arXiv 上的 PDF 或网页（如 PMC 页面，保存为 .html 后同样处理，视为单页，引文页码记为 1）：`extract_pdf.py prior.pdf --id 名称 --role comparator --out WORK/sources --rel-to WORK --public-date YYYY-MM-DD --date-note "依据"`。日期按 manual 记录，校验器会给出警告提醒人工核对。把打印的条目加入 `review.json` 的 `sources`（或子代理的 `C*.json` 的 `sources`）。

### 2. 生成任务简报
`python3 scripts/novelty.py plan WORK/contributions.json --out WORK/novelty`
会为每个贡献写一份 `C*.brief.md`（包含下面的子代理协议、检索预算和输出格式），以及 `plan.json`。

### 3. 并行启动子代理
每个贡献一个子代理，**在同一轮中同时发出**。给子代理的提示词就是对应 brief 的全文，再加上技能目录的绝对路径。不要把论文全文交给子代理；它只需要贡献描述和公开前作。

### 4. 子代理协议（brief 中已包含）
1. 检索至少四类查询：任务术语、机制术语、替代说法、**上位问题**（把方法抽象成它要解决的经典问题，例如“让机器人从人类示范中学会操作”→ 模仿学习/行为克隆、逆强化学习；“把低分辨率视频变清晰”→ 视频超分辨率、盲图像复原）；每类至少两个数据源（`scripts/sources.py`：arxiv、openalex、semantic、dblp、crossref；外加宿主的网页检索）。
2. 对已知的最相关论文做引用追溯：`sources.py semantic-refs ID` / `semantic-cites ID`。
3. 合并去重：`sources.py merge *.json --out candidates.json`，筛选出不超过 5 篇候选。
4. 对最接近的 1–3 篇，下载截止日期前的版本全文：`scripts/fetch_arxiv.py ARXIV_ID --version vN --out WORK/sources`，读方法部分。
5. 写 `WORK/novelty/C*.json`（格式见下），引文必须逐字来自快照，并带页码。
6. 只报告读到的内容；没读全文的候选标 `read: false`，不能作为 overlap 的依据。

### 5. 主代理：合并与复核
`python3 scripts/novelty.py merge WORK/novelty --review WORK/review.json`
- 校验每个 `C*.json` 的引文与页码、日期是否不晚于截止日期，跨贡献合并前作来源，写入 `review.json` 的 `novelty` 和 `sources`。
- 主代理必须亲自读：所有 `overlap` 结论涉及的双方段落；不同子代理对同一前作给出矛盾描述的情况。
- 把结论回填到 `summary.contributions[].novelty_assessment`，必要时写成 `dimension: "novelty"` 的意见。

## 子代理输出 `C*.json`
```json
{
  "contribution_id": "C1",
  "searches": [{"provider": "arxiv", "query": "...", "file": "search/C1-arxiv-1.json", "hits": 10}],
  "candidates": [{"title": "...", "arxiv_id": "2501.13928", "version": "v1", "public_date": "2025-01-23",
                   "read": true, "source_id": "fast3r-v1", "relevance": "high"}],
  "comparisons": [{
    "comparator_id": "fast3r-v1",
    "common_mechanism": "...", "differences": "...",
    "problem": "...", "assumptions": "...", "guarantee_or_insight": "...", "resources": "...",
    "manuscript_side": "用公开术语描述的稿件机制（不得粘贴保密原文）",
    "comparator_quotes": [{"quote": "...", "page": 3}],
    "delta_significance": "...", "strongest_counterargument": "..."
  }],
  "assessment": "distinct",
  "uncertainty": "未检索到 X 方向的工作；Semantic Scholar 限流",
  "coverage": "检索到的范围与未覆盖的部分"
}
```

## 写成审稿意见时
- `overlap` 的确认意见：同时引用稿件原文与前作原文，说明在相同假设下二者等价的理由，给出前作版本和日期。
- `incremental`：说明增量体现在哪里、是否足以支撑论文的声明（例如“首次”的说法需要修正，但贡献本身成立）。
- 同期工作（截止前约 3 个月内公开）：可以要求讨论，不应作为否定创新性的依据，并注明“同期”。
