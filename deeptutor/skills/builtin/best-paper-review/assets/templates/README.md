# 输出模板

| 文件 | 用途 | 使用者 |
|---|---|---|
| `comments_submission.html` | 英文提交稿页面模板，占位符 `{{BPR_TITLE}}`、`{{BPR_VENUE}}`、`{{BPR_VERSION}}`、`{{BPR_DATE}}`、`{{BPR_RULES}}`、`{{BPR_PAYLOAD}}`、`{{BPR_KATEX_CSS}}`、`{{BPR_KATEX_JS}}` | `scripts/submission.py build`、`scripts/finalize.py` |
| `submission_en.template.json` | 英文提交稿数据骨架（字段、三个详略版本、推荐数量、表单顺序） | `scripts/submission.py skeleton` |
| `comments_submission.template.md` | 从页面复制出的 Markdown 稿件形态 | 参考 |
| `pdf_reader/` | PDF 阅读器（默认的 `review-viewer.html`）：引文高亮、页边标记、缩略图、论文速览、KaTeX 公式；只读 | `scripts/package_viewer.py`、`scripts/finalize.py` |
| `pdf_viewer/` | 可编辑的 PDF 批注查看器（PDF.js / pdf-lib 也放在这里，两种页面共用） | `scripts/serve.py`、`scripts/package_viewer.py --editor` |

写作规范见 `references/submission.md`，查看器用法见 `references/viewer.md`。
公式渲染使用 `assets/vendor/katex/`（KaTeX 0.16，MIT，字体以 data URI 内联），页面不访问网络。

修改页面样式时只改 `comments_submission.html`；占位符必须各保留一处以上，`submission.py build` 会拒绝留有未填占位符的输出。
