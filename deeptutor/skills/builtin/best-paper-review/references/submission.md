# 英文提交稿写作规范（submission_en.json → comments_submission.html）

完整/自查模式在 `review.json` 定稿（复核与总评完成）之后，写一份可以直接粘贴进会议审稿表单的英文稿 `WORK/submission_en.json`，再由 `scripts/finalize.py` 生成 `comments_submission.html`。`review.json` 是中文工作记录，英文稿是它的**译写**，不是第二次审稿：不新增结论、不改变定级、不改分。

```bash
python3 scripts/submission.py skeleton WORK/review.json WORK/submission_en.json   # 从模板起稿：编号、链接、分数已填好
python3 scripts/submission.py check WORK/submission_en.json                       # 逐条检查，必须通过
python3 scripts/finalize.py WORK --pdf paper.pdf --out reviews/                   # 生成标准输出目录
```

模板与样例：`assets/templates/submission_en.template.json`（数据骨架）、`assets/templates/comments_submission.template.md`（复制出的 Markdown 形态）、`assets/templates/comments_submission.html`（页面模板）。

## 1. 语言与字段

- 全文英文，不出现中文。语气客观、具体、对事不对人；用 "the paper / the authors"，不用 "you"。
- 默认字段顺序：Summary → Soundness → Presentation → Contribution → Strengths → Weaknesses → Questions → Flag For Ethics Review → Rating → Justification → Confidence → Code Of Conduct。
- 目标会议的表单不同（如另有 Significance、Originality、Limitations）时，在 `form` 中按该会议的顺序列出 `[key, Label]`；评分类字段的值写在 `scores` 中。`skeleton` 会按 `overall.subscores` 自动调整。

## 2. 每个字段怎么写

**Summary**：一段话，约 80–200 词。只陈述论文做了什么：问题、方法、实验设置、主要结论，不作评价。

**Strengths / Weaknesses / Questions**：**每一点一段话**，不在一点内分行、不嵌套列表。按重要性从高到低排列：

- Strengths 默认推荐前 **3** 点，Weaknesses 默认推荐前 **6** 点（`recommend`），其余作为候选折叠在页面中，审稿人可一键加入。Questions 全部纳入。
- 所有 major 的确认问题/证据局限必须出现在 Weaknesses 中，并且排在推荐范围内。
- **聚焦（v1.5）**：Weaknesses 按对核心主张的影响排序——先写切断证据链的（`core_claims.impacts` 为 `cut`，或 `tier` 为 A），再写收窄主张的（`narrow`，或 `tier` 为 B），最后写其他问题。`tier` 为 C 的写作与呈现问题（措辞夸大、不影响结论的数字或表格笔误、细节缺失）合并为一条 “Presentation and consistency”，放在最后，不单独占用推荐位；只有一条 C 时可单独列出。
- Weakness 一段的结构：问题是什么 → 证据在哪里（Table/Figure/Section/Eq.）→ 为什么影响论文的结论 → 作者可以怎样解决。
- Question 以问句结束，问的是答案会改变评估的内容，不问论文里已经写明的事。

**Justification**：一段话，先指出最弱的一环——哪条核心主张、哪段证据链最可疑或已被切断（没有则明说核心主张都有支撑）——再说明评分由哪几条决定性的优点与不足决定，以及什么证据会让分数上调或下调。与 `overall.justification` 和 `decisive_ids` 一致。

**评分**：`scores.rating`、`scores.confidence` 与各子分的数字必须等于 `review.json` 的 `overall`；标签用目标会议当年表单的英文原文（形如 `6: marginally above the acceptance threshold`）。标签未核实时，在 `venue_note` 中说明。有 `ethics_flag` 的意见时，`scores.ethics` 写明需要伦理审查的理由。

## 3. 三个详略版本

每个 Strength / Weakness / Question 以及 Justification 都写 `short`、`medium`、`long` 三个版本，页面可统一切换，也可随机混合。三个版本说的是**同一个判断**，只是展开程度不同，不得在长版本里加入短版本没有的新结论。

| 版本 | 句数 | 参考词数 | 内容 |
|---|---|---|---|
| short | 1–2 句 | 约 15–55 | 判断 + 最关键的一个证据位置 |
| medium | 3–5 句 | 约 40–120 | 判断、证据与位置、影响、具体请求 |
| long | 6–10 句 | 约 75–260 | 再加上背景、与前作的比较、作者最有力的辩护及其为何不足 |

`check` 会对词数越界与“长短倒置”给出警告。

## 4. 与 review.json 的对应

- 每一项有唯一 `id`（S01、W01、Q01…，只在数据中使用），并用 `from` 列出它对应的 `review.json` 意见编号；`id` 与某条意见相同时可省略 `from`。由创新性或复核得到的意见（如 G01）同样用 `from` 链接。
- Strengths 只链接 `strength`，Weaknesses 链接确认问题、证据局限或改进建议，Questions 链接澄清问题；可以合并多条意见为一项。
- `review.json` 中撤回（withdrawn）的意见不得出现。

## 5. 正文中不得出现的内容

- 内部编号（S01、W05、N03、G01 等）、文件名与流程记录（review.json、critique、复核、指纹、规则版本）。
- 使用者的表态或与作者的关系（见 `references/neutrality.md`）；提交稿只依据论文证据。
- 未经核实的推测性指控；涉及学术诚信的问题按 `references/ethics.md` 写入伦理栏。

## 6. 公式与 Markdown

- 数学一律写成 LaTeX：行内用 `$...$`，独立公式用 `$$...$$`；字面美元符号写 `\$`。例：`$3P\,|w|$`、`$\mathcal{P}$-Adam`、`$\theta_{\mathrm{FM}}$`、`$-13.6 \pm 5.6$`、`$p < 0.01$`、`$19\times$`。
- 不要在公式外直接使用希腊字母、±、×、≤ 等 Unicode 数学符号（`check` 会警告），单位符号（如 ‰、%）可以直接写。
- 可用的 Markdown：`**粗体**`、`*斜体*`、`` `代码` ``、`[链接](url)`。不使用标题、表格、图片与多级列表。
- 页面复制时，列表字段自动输出为 `1. ` 编号列表，整份稿件输出为 `**Label:**` 加内容的 Markdown。

## 7. 输出目录

`finalize.py` 把 WORK 复制成标准目录（WORK 本身不改动、不删除）：

```
reviews/<YYYYMMDD>_<Paper_Title>/          # 日期取 review_date；标题中非字母数字字符换成下划线
├── comments_submission.html               # 英文提交稿（可编辑、可复制）
├── review-viewer.html                     # PDF 阅读器：意见高亮、论文速览（离线单文件）
├── source/                                # review.json、review.md、form.md、submission_en.json
└── review_process_files/                  # 贡献清单、创新性检索、复核、快照等过程文件
```

- 同一篇论文再次测试时，目录名自动加后缀 `_2`、`_3`……
- 外层只放两个 HTML，不再生成 `review.html`。
- 不需要英文稿的情况（如修改闭环模式、或使用者明确不需要）在 `policy.submission_skipped` 写明原因，此时目录中没有 `comments_submission.html`。

## 8. 页面功能（comments_submission.html）

- 详略切换：简略 / 中等 / 详细 / 随机混合（可重新随机），每一项也可单独切换。
- 每段为 Markdown，可双击或点“编辑”修改，实时预览，Esc 结束；修改保存在浏览器本地，可还原。
- 每个字段有复制按钮（列表复制为编号列表），可复制整份稿件，或导出 Markdown / 合并修改后的 `submission_en.json`。
- 搜索框可在稿件内查找；公式由内置 KaTeX 渲染，不需要联网。
