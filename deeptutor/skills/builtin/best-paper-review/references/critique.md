# 审稿意见复核

审稿写完后，要审查的对象换成**审稿意见本身**：有没有读错原文、批评是否公平、轻重是否得当、有没有漏掉整类问题或整条文献线。目标不是再找更多缺点，而是减少误读、误报和漏报。

复核分两层：脚本做确定性检查（`critique.py lint`），独立的复核子代理做语义检查（`critique.py plan` 生成任务简报）。主审稿人逐条回应全部复核意见，回应结果写入 `review.json` 并在渲染时公开。

## 流程

```bash
python3 scripts/critique.py lint  WORK/review.json                      # 确定性检查，结果直接并入 review.json
python3 scripts/critique.py plan  WORK/review.json --out WORK/critique  # 生成复核子代理的任务简报
# 启动一个复核子代理，提示词为 WORK/critique/critic.brief.md 全文（新上下文，不要附带你的推理过程）
python3 scripts/critique.py merge WORK/critique/critique.json --review WORK/review.json
# 逐条处理 review.json 中 critique.findings 的 resolution 与 response，按需修改意见
python3 scripts/evidence.py WORK/review.json --strict                   # 未回应的复核意见会被拦下
```

没有子代理能力时，由你在**重新通读论文之后**按同一份简报自行复核，并在 `critique.critic` 中写明“主审稿人自复核”，其独立性弱于子代理。

## 独立性要求
- 复核者只拿到：论文 PDF 与快照、渲染后的审稿意见（`review.md`）、目标会议与截止日期。不提供主审稿人的草稿、撤销记录以外的思考过程或对作者的印象。
- 复核者与主审稿人是同一模型时，仍可能共享同样的错误。复核结论是线索，主审稿人必须回到原文核实后再接受或拒绝。
- 同样不得检索或阅读该论文的公开评审、讨论与评分。

## 复核者要检查什么

### A. 逐条核对现有意见
1. **误读**（`misread`）：重读意见引用的页面与上下文，论文是否真的这么说？数字、符号、方向是否抄对？
2. **论文已回应**（`already_addressed`）：全文与附录是否已经回答了这条批评？
3. **证据不足**（`unsupported`）：引文能否支撑结论？是否把“未报告”当成“没做”？
4. **轻重不当**（`severity`）：major/minor、confirmed/limitation 与 `tier`（A/B/C）的定级是否与影响相符？两个方向都要查：偏重——查实的笔误、措辞夸大是否被标成 A/B（G2），多条 C 是否被叠加成核心缺陷（G8），“说过头”是否被误判为“不成立”；偏轻——推翻核心结论的问题是否被标成 B/C，`overall.core_claims` 的改写是否超出证据能支持的范围，证据链已被切断时是否仍判“成立”；切断与收窄是否分对（缺基线、缺消融通常是收窄，不应据此判“不成立”；在公平条件下增益消失才是切断）；分数是否落在判定对应的 G7 范围内。见 calibration.md“评分底线条例”。
5. **表述空泛或语气不当**（`vague`、`tone`）：是否给出了具体位置与可执行请求？是否有贬低作者的措辞？
6. **重复**（`duplicate`）：多条意见是否在说同一件事？

### B. 找出遗漏
7. **遗漏问题**（`missing_issue`）：独立按九维度清单再查一遍，记录主审稿人没提、但有原文证据的问题。
8. **遗漏文献线**（`missing_literature`）：把方法抽象成它要解决的**上位问题**（例如“让机器人从人类示范中学会操作”→ 模仿学习/行为克隆、逆强化学习；“把低分辨率视频变清晰”→ 视频超分辨率、盲图像复原），检查审稿是否讨论过这条经典路线。可用 `sources.py` 检索，日期须早于截止日期。
9. **修订内容**：若论文含修订标记（如红字、“revised”“new in rebuttal”），答辩期间新增的分析是否真的回答了原问题、是否与表格一致？

### C. 整体一致性
10. **总评与意见不一致**（`inconsistent`）：分数是否与决定性问题匹配？优点是否被忽视？
11. **公平性**：是否用了不适合该论文类型的标准？是否苛求截止日期后的工作？

## 复核结果格式（`critique.json`）
```json
{
  "critic": "独立复核子代理（新上下文）",
  "date": "YYYY-MM-DD",
  "findings": [
    {"id": "K01", "type": "misread", "target": "W05", "severity": "major",
     "detail": "具体说明哪里读错、为什么",
     "evidence": [{"source_id": "paper", "quote": "逐字引文", "page": 21}],
     "suggestion": "如何修改该意见"}
  ],
  "summary": "两三句总体判断"
}
```
`target` 为意见编号、`overall`、`novelty`，或 `missing`（遗漏类）。引文必须逐字来自快照，脚本会核对。

## 主审稿人如何回应
每条复核意见都要填写：
- `resolution`：`accepted`（采纳，并已修改对应意见）、`partial`（部分采纳）、`rejected`（不采纳）；
- `response`：一两句理由；拒绝时说明依据（引用原文更好）。

采纳后要真的去改 `comments`、`overall` 或 `dimensions`，然后重新运行 `evidence.py` 与 `render.py`。渲染结果中会附“审稿意见复核”一节，列出复核意见与处理结果。
