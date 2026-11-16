# Native paper review

The PDF reader's **Review** tab and the writing editor's **Reviews and responses**
use the same native chat runtime as the rest of DeepTutor. They offer author
preflight, simulated peer review, quick review, response assessment and revision
assessment. Results are drafts for the author to assess; they do not predict a
conference's decision.

## Installed skill

`deeptutor/skills/builtin/best-paper-review` contains the complete upstream
[Best-Paper-Review-Skill](https://github.com/gulucaptain/Best-Paper-Review-Skill)
v1.5.1 at `4f4e7d50af17290fc9cb908821db13098de09361`. Its 247 original
files are unchanged. `PROVENANCE.json` records every upstream file hash, and
`LICENSE` preserves the repository's MIT notice. Embedded PDF.js, pdf-lib,
KaTeX and CMaps keep their own licenses. The adapter instructions are separate
from the upstream rule fingerprints.

The shipped conference forms belong to this release. They are not automatically
updated official submission requirements. DeepTutor's language preference and
permission boundaries take precedence over skill prose. The skill's standalone
HTML viewer is an optional artifact; the product retains its native PDF reader.

## Frozen target and durable continuation

Creating a review fixes an edition revision and original PDF SHA-256. A writing
review first bridges a successful build to a traceable library edition. A review
has exactly one server-bound native session; retries reuse the request receipt.
Each turn explicitly selects `best-paper-review` in its resource snapshot and
attaches the original plus the `paper_review` reference. Normal durable turns
own cancellation, reconnection, branching and retry.

`paper_review_checkpoint` has three actions:

- `read`: prepare the upstream scaffold or copy the last saved checkpoint, then
  return `review.json`.
- `save`: accept the complete updated JSON, validate its frozen identity and
  quoted originals, then save a checkpoint.
- `checkpoint`: save changes made in the turn's staged review directory by an
  available executor.

The first two actions do not need a general script executor. Initialization and
validation use fixed commands from the verified bundled package. A same-named
account or workspace skill cannot silently replace these host-side scripts.
Where generic execution is configured, a successful `read_skill` of `SKILL.md`
admits the complete package to a read-only mount. Every subsequent execution
rechecks access; installing a package does not enable the executor.

PDF input and server state remain outside the writable work area. Each saved
checkpoint records the previous hash; a competing stale turn cannot overwrite a
new checkpoint. New turns copy the latest committed review. Author responses
are separate from AI output and marking an issue addressed does not verify its
resolution. Source changes still use the editor's existing selection, proposal
diff and explicit acceptance flow.

## Meaning of checks

Quote matching is checked against the frozen PDF, independently of model-edited
text snapshots. Invalid physical pages, invented quotes and changed PDF hashes
are rejected. A matched quote proves occurrence, not the argument built on it.
Unlocated suggestions remain visibly unlocated.

Upstream structure/evidence validation is recorded separately from scientific
judgments. Missing external retrieval, publication-date verification, figure
inspection or independent critique stays pending. No lint result or model-written
status can certify scientific correctness. The nine review dimensions and
counterevidence requirements remain part of the skill workflow.

## Acceptance

Focused offline tests cover authorization, skill shadowing, frozen targets,
malformed artifacts, quote checks, CAS continuation, native session binding,
read-only staging and replay resource selection. A real model smoke test uses
public HOI PDFs and the existing local Codex OAuth login:

```sh
python scripts/evaluate_paper_review.py \
  --home /path/to/new-scratch-home \
  --sources /path/to/public-pdf-fixtures \
  --output /path/to/acceptance-receipts
```

The fixture directory uses the same four public PDFs as
`evaluate_continuity.py`. The runner performs a quick review and a second turn
that refines one suggestion, checking saved IDs, PDF anchors and checkpoint
history. It does not claim that a quick smoke test validates a full scientific
review or a user's unpublished manuscript.

## Local deployment ports

`scripts/study.py run` and `doctor` accept `--backend-port` (default `8001`)
separately from the frontend `--port` (default `3782`). Both ports must be
between 1 and 65535 and must differ. If another project owns port 8001, choose
an unused backend port without stopping that project:

```sh
python scripts/study.py run --home /path/to/study-data --backend-port 8015 --port 3782 --codex-login
```

The frontend uses relative API and WebSocket URLs; its server proxy prefers
the runtime `DEEPTUTOR_API_BASE_URL`. Changing the backend port therefore does
not require rebuilding the current frontend, and the default build's 8001
fallback remains compatible. The launcher uses the selected backend port for
its process, readiness probe and child-process environment.
