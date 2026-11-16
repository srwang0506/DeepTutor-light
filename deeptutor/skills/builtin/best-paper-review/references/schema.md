# review.json（schema v2）

路径一律相对于 review.json 所在目录。`evidence.py` 按本文件校验；`--strict` 下所有“建议”级规则也会变成错误。

## 顶层

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | `2` | 必填 |
| `language` | `"zh-CN"` | 审稿正文语言 |
| `mode` | `quick` / `full` / `self` / `rebuttal` | 决定覆盖率规则 |
| `paper` | object | `title`、`version`（如 `arXiv v1`）、`pdf`（相对路径）、`pdf_sha256`、`pages`、`paper_type`（见 paper-types.md 的键）、`domain` |
| `venue` | object / null | `id`（`assets/venues.json` 中的键，如 `ICML`）、`year`、`track` |
| `cutoff` | ISO 日期 | 创新性截止日期 |
| `review_date` | ISO 日期 | |
| `policy` | object | `blind`（是否未看过他人评审）、`read_scope`（实际读了什么）、`not_done`（未做的事列表）、`searches`（检索记录摘要）、`withdrawn`（撤销/降级的候选批评：`[{candidate, action, reason}]`）、`prompt_injection`（发现的注入文字摘要）、`user_stance`（使用者表态：`{kind: [none/outcome/score/relationship/selective/pressure], summary, handling}`，必填）、`neutrality`（`{symmetry_check, briefs_checked}`，完整/自查模式出分时必填），见 neutrality.md；`submission_skipped`（不写英文提交稿的原因，字符串；完整/自查模式缺少 `submission_en.json` 时必填） |
| `summary` | object | `one_line`、`problem`、`method`、`setup`、`contributions`（列表，见下） |
| `overall` | object | `recommendation`（中文标签）、`score`、`confidence`（1–5）、`subscores`（如 `{"soundness":3}`）、`justification`、`decisive_ids`（决定评分的意见编号）、`would_change`（什么情况下评分会变）；v1.5 新增：`core_claims`（`[{claim, quote, page, type, evidence_chain, impacts: {意见编号: cut/narrow/none}, restated, verdict: holds/partly/fails}]`，最多 3 条）、`significance`（high/medium/low）、`blocking_issue`（score=3 时必填；v1.5.1 起也可写“贡献重要性不足”及理由）、`g13_check`（反向校验一句话）。分数统一用 1–6 量表；v1.5.1 起分数范围只由 `core_claims` 判定决定（calibration.md G7），`evidence.py` 检查分数是否在范围内、`impacts` 与 `verdict`/`tier` 是否自相矛盾 |
| `dimensions` | object | 九个维度键 → `{status, comment_ids, note}` |
| `sources` | list | 见下 |
| `comments` | list | 见下 |
| `numeric_checks` | list | 见下 |
| `novelty` | list | 由 `novelty.py merge` 写入的逐贡献比较 |
| `critique` | object | 审稿意见复核（`critique.py` 写入）：`critic`、`summary`、`abstractions`（上位问题）、`findings`（`{id, source: lint/critic, type, target, severity, detail, evidence, suggestion, resolution, response}`）。每条 finding 都必须有 `resolution`（accepted / partial / rejected）与 `response`，否则校验不通过；完整/自查模式缺少 `critique` 时 `--strict` 报错 |
| `provenance` | object | `init_review.py` 写入：`skill_version`、`rules_sha256`（中立性等规则文件的合并指纹）、`rules_official`（与本地清单一致为 `true`）。用于对照官方发布指纹，判断审稿是否在未修改的规则下生成 |
| `rebuttal` | list | 仅 rebuttal 模式：`{concern, reviewer, response_summary, verdict: resolved/partial/unresolved, comment_ids}` |

### 贡献 `summary.contributions[]`
`id`（C1…）、`claim`（中文概括）、`quote`（作者原话，须在 manuscript 快照中）、`page`、`type`（method/system/empirical/theory/dataset/benchmark/analysis）、`novelty_assessment`（distinct / incremental / overlap / unresolved / na，merge 后填；`na` 表示实证类贡献未做前作检索）。

### 维度 `dimensions`
键固定：`problem` 问题与动机、`novelty` 创新性、`soundness` 方法正确性、`theory` 理论、`experiments` 实验设计与支撑、`baselines` 基线与公平性、`reproducibility` 可复现性、`clarity` 写作与图表、`limitations` 局限与伦理。
`status`：`issues`（`comment_ids` 至少一项，且这些意见的 `dimension` 与之相符）、`ok`（`note` 必须写明检查范围）、`na`（`note` 写理由）、`unchecked`（仅 quick 模式允许，且须在 `policy.not_done` 提及）。

## 来源 `sources[]`

| 字段 | 说明 |
| --- | --- |
| `id` | 唯一 |
| `role` | `manuscript` / `supplement` / `comparator` / `other` |
| `path` | 快照文本（由 `extract_pdf.py` / `fetch_arxiv.py` 生成） |
| `manifest` | 快照清单 `*.manifest.json`：PDF 哈希、抽取工具、每页起止偏移、文本哈希 |
| `sha256` | 快照文本哈希 |
| `url`、`version`、`arxiv_id`、`doi` | 版本信息 |
| `public_date` | 该版本公开日期（ISO）；未知填 null |
| `date_verified` | `verify_dates.py` / `fetch_arxiv.py` 写入：`{method, value, checked_at, url}`。用户提供、无法在 arXiv/Crossref 查到的 PDF 可写 `{"method": "manual", "value": 日期, "note": 依据}`，校验器会给出警告 |

## 意见 `comments[]`

| 字段 | 说明 |
| --- | --- |
| `id` | 如 `W01`、`S01`、`Q01` |
| `title`、`body` | 中文；`body` 可用空行分段，公式写成 `$...$`（见 calibration.md 写作规范） |
| `dimension` | 九维度键之一 |
| `status` | `strength` 优点 / `confirmed` 确认问题 / `limitation` 证据局限 / `question` 澄清问题 / `suggestion` 改进建议 |
| `severity` | `major` / `minor`（`strength` 可省略） |
| `tier` | v1.5：`A` 致命 / `B` 实质 / `C` 可修改（`strength` 省略），定义见 calibration.md G1–G5；与 `severity`、`confidence` 分开判断 |
| `confidence` | `high` / `medium` / `low` |
| `scope` | `global` / `page` / `region`；后两者需 `page`（从 1 开始） |
| `rect` | region 用，`[x, y, w, h]`，相对未旋转页面归一化到 0–1 |
| `evidence` | `[{source_id, quote, page}]`；`page` 为该引文所在页，脚本会核对 |
| `countercheck` | 查过哪些地方、作者最有力的辩护、为何仍成立 |
| `request` | 对作者的具体可执行请求 |
| `comparator_ids`、`comparison` | 创新性确认意见必填 |
| `numeric_check_ids` | 引用的数值复算 |
| `resolution` | rebuttal 模式下：resolved / partial / unresolved |
| `ethics_flag` | `true` 表示学术诚信/伦理问题（如提示注入）；会议表单中只出现在伦理栏 |

**确认意见（`confirmed`）的硬性要求**：至少一条 manuscript 引文且页码正确；`countercheck` 非空；`scope` 为 page/region 时，`page` 须与某条 manuscript 引文页一致。
**创新性确认意见**：另需 `comparator_ids` 非空、`comparison` 非空、每个前作都有引文、前作 `public_date` 早于等于 `cutoff`；`--strict` 下前作须有 `date_verified`。

## 数值复算 `numeric_checks[]`

`{id, claim, expression, value, claimed, tolerance, evidence}`。
- `expression`：用论文中的原始数字写出的算式，只允许数字、`+ - * / ** ( )` 和 `abs/min/max/round/sqrt/log`；
- `value`：你复算得到的结果，脚本会重新计算 `expression` 并与它比较（默认容差 0.05 或相对 0.5%）；
- `claimed`（可选）：论文中声称的数字。与复算不符正是常见的数值批评（例如摘要写 52%，复算为 38.1%），脚本不会因此报错，渲染时并列显示；
- `evidence`：输入数字所在的引文。

## 英文提交稿 `submission_en.json`（与 review.json 同目录）

写作规范见 `submission.md`，骨架见 `assets/templates/submission_en.template.json`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `language` | `"en"` | 固定 |
| `venue_note` | string | 分数标签的来源与是否核实 |
| `summary` | string | 一段话 |
| `scores` | object | `rating`、`confidence` 必填，数字与 `overall` 一致；子分键与 `overall.subscores` 一致；另有 `ethics`、`code_of_conduct` 等表单值 |
| `form` | `[[key, Label], ...]` | 可选，表单字段顺序；默认 Summary…Code Of Conduct |
| `recommend` | object | `{strengths: 3, weaknesses: 6}`，默认纳入的数量 |
| `strengths` / `weaknesses` / `questions` | list | 按重要性排序；每项 `{id, from?, title, severity?, short, medium, long}`，`from` 为对应的 `comments[].id` |
| `justification` | object | `{short, medium, long}` |

## 示例

见 `examples/demo/review.json`。
