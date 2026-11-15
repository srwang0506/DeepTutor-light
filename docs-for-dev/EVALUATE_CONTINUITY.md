# Tutor continuity evaluation

From the checkout, use the project's Python environment with the Studio and
server dependencies installed. Sign in to Codex locally before running a mode
that uses the model. The evaluator uses the existing Codex OAuth session.

```bash
python scripts/evaluate_continuity.py \
  --home /path/to/new-scratch-runtime \
  --sources /path/to/public-pdf-fixtures \
  --output /path/to/evaluation-reports
```

Use separate directories. `--home` must be new or empty on each run. The
evaluator creates its own projects, question notebook and Studio catalog there.
It seeds fresh fixtures and places each report under a new subdirectory of
`--output`, so you can reuse the same report parent across runs.

Supply these four public PDFs yourself. Keep their original page order, since
source checks use one-based physical PDF pages.

| Filename | Document |
| --- | --- |
| `dsa_bfs.pdf` | MIT 6.006 lecture notes on breadth-first search. Page 4 explains increasing distance levels. |
| `dsa_dijkstra.pdf` | MIT 6.006 lecture notes on Dijkstra's algorithm. Imported as a companion to the BFS notes. |
| `hoi_qpic.pdf` | QPIC paper, with the method on page 3. |
| `hoi_gen.pdf` | GEN-VLKT paper, with the method on page 5 and ablation Table 4(b) on page 9. |

`--mode all` runs the following workflows. Pass one mode to run it on its own.

- `baseline` asks a BFS question through the normal streaming SDK.
- `sources` uses selected PDF pages, follows up in the same conversation, and
  reads an immutable log snapshot. The SSH transport is simulated by a local
  subprocess. The fixture log describes no real training run.
- `practice` saves and corrects an answer through the practice ASGI routes in
  process, then checks both versions via Tutor. It advances the scheduler clock
  to exercise the later independent-review condition.
- `resume` verifies that a saved physical PDF page is available when returning
  to its project and stays out of another project. This mode runs locally
  without a model.

Model modes send task prompts, selected public text and synthetic fixtures to
Codex and use the signed-in account's model allowance.

Open `summary.json` for structural checks and the per-case receipt filenames.
Each model receipt includes the persisted answer, call/result metadata and
matched citation URLs, along with a checklist marked `needs_human_review`.
Review the answer and the source passages against that checklist. The exit
status covers automated checks only. A passing run shows that the tested
continuity mechanisms worked and that the expected links were emitted. The
quality of the answers still requires review, and the simulated scheduler
checks the software's retention workflow.
