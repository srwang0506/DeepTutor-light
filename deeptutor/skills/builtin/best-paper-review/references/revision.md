# 作者修改闭环（`revise` 模式）

面向作者：把审稿意见（本技能生成的，或会议返回的外部审稿意见）变成**问题看板 → 修改计划 → 新版本核验 → 回复信**，并区分“答应要改”和“文件里真的改了”。

## 流程

```bash
# 1. 建看板：来自本技能的 review.json；外部审稿意见先整理成 reviews.json（格式见下）
python3 scripts/revision.py board WORK/review.json --out WORK/revision/issues.json --external WORK/revision/reviews.json

# 2. 逐条填写 plan.action / plan.cost / plan.location，按优先级排序
python3 scripts/revision.py plan WORK/revision/issues.json

# 3. 作者修改论文后，抽取新版本快照，查看新旧差异
python3 scripts/extract_pdf.py revised.pdf --id revised --role other --out WORK/sources --rel-to WORK
python3 scripts/revision.py diff WORK/sources/paper.txt WORK/sources/revised.txt --out WORK/revision/diff.json

# 4. 每个已改的问题把 status 设为 done，在 verification.evidence 中引用新版本里的修改原文；然后核验
python3 scripts/revision.py verify WORK/revision/issues.json --old WORK/sources/paper.txt --new WORK/sources/revised.txt

# 5. 生成回复信（默认英文，可 --lang zh），检查字数上限；生成看板报告
python3 scripts/revision.py letter WORK/revision/issues.json --lang en --limit 5000 --out WORK/revision/response.md
python3 scripts/revision.py report WORK/revision/issues.json --out WORK/revision/board.md --html
```

## 状态
| 状态 | 含义 |
| --- | --- |
| `open` 未处理 | 刚建立 |
| `planned` 已计划 | 已确定修改方式与成本 |
| `done` 作者称已完成 | 等待核验 |
| `verified` 已核验 | 脚本在新版本中找到了**新增**的修改文字 |
| `failed` 核验失败 | 找不到引文、页码不符，或引用的文字原版本中就有 |
| `wontfix` 不修改 | 必须在 `plan.note` 或 `response` 中写明理由 |

**闭环检查**：所有主要（major）问题都是 `verified` 或带理由的 `wontfix` 时才算通过。

## 核验规则
- `verification.evidence` 中每条引文默认 `kind: "added"`：必须出现在新版本快照中，且**不能**出现在原版本中——这样才能证明是新改的。
- 删除类修改（例如删去过度声明或隐藏文字）标 `kind: "removed"`：引文必须出现在原版本中、且不再出现在新版本中。
- 若修改方式是“指出原文已有说明”（澄清类），引文标 `kind: "existing"`：只要求在新版本中存在。回复信中应说明原文位置，而不是声称做了修改。
- 补实验类修改要引用新增的结果描述或表注，不能只引用回复信里的承诺。
- 脚本只核验“文字确实改了”，不核验“改得是否充分”。是否真正回应了问题，仍需你逐条判断；必要时再跑一次 `rebuttal` 模式评估。

## 修改方式与优先级
`plan.action`：`clarify` 澄清/改写、`experiment` 补实验、`analysis` 补分析、`rewrite` 重写表述、`cite` 补引用、`fix` 修正错误、`other`。
`plan.cost`：`low` / `medium` / `high`。优先级 = 严重度权重（主要 3、次要 1）× 成本权重（低 3、中 2、高 1）。先做“影响大、成本低”的修改。

处理原则（来自 calibration.md 与 false-positives.md）：
- 能通过澄清或收窄声明解决的问题，不要机械地追加实验；
- 不能为了满足一条意见而扩大论文范围；
- 不同意的意见可以 `wontfix`，但要给出有证据的理由，回复信中礼貌说明。

## 外部审稿意见格式（`reviews.json`）
会议返回的审稿意见由你逐条拆分整理为：
```json
[{"reviewer": "Reviewer 6MXH", "title": "缺少与其他生成模型的对比", "text": "原文要点……", "severity": "major", "dimension": "baselines"}]
```
一条审稿意见里有多个问题时拆成多条；严重度按 calibration.md 判断，不照搬审稿人的语气。

## 回复信
`letter` 按审稿人分组，每个问题包含：问题摘要、`response`（你写的回复正文）、新版本中的修改位置与引文。`--limit` 检查每位审稿人的字符数（OpenReview 常见上限 5000）。回复正文用目标会议的语言（通常英文）；看板与报告用中文。
