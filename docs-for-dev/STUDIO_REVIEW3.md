# Personal Studio: continuity, integrations and evaluation

This revision extends the contracts from [the integrity review](STUDIO_REVIEW2.md).
It keeps the restored interface, workspace isolation and modular monolith.

## Where to use it

| Entry | Capability |
| --- | --- |
| Library → Sources | Incremental, read-only Zotero metadata synchronization |
| Experiments → Sources / a run | Append-only JSONL tracking and paged metric history |
| Evidence → Literature review | Frozen protocol, abstract/full-text screening, anchored extraction |
| A completed or explicitly revealed question | Review its concept, method, conditions and family |
| Connections → Project transfer | Encrypted export, conflict preview and explicit import decisions |
| A PDF or existing question → Save offline | Original PDF / prompt-only question, local draft, explicit reconnect submission |
| Connections → Quality evaluation | Freeze a task suite, run the configured model and export every receipt |

### Zotero

Provide a user/group library ID and a key authorized to read that library. Keys
are encrypted locally, never returned by the API or included in portable project
exports. This is metadata synchronization: attachment metadata and source originals
are retained, but PDF downloads and Zotero writes are not performed.

The library version cursor advances in the same transaction as imported changes.
Deleted items retain tombstones; deletion upstream does not erase a paper shared
by local projects. Replays retain object identities and author edits. A changed
library version during a page fetch aborts the batch; retry starts from the last
committed cursor. Rate limits preserve retry timing. Revoked access disables the
source; editing its key reauthorizes it. All reads are opt-in and admin gated.

### Local experiment logs

Choose a single absolute `.jsonl` or `.ndjson` path in Experiments. The source
checks file descriptors, rejects symlinks and follows complete newline-terminated
records. An appended partial line remains unread until completed. Rotation and
truncation are detected. Stable `event_id` values deduplicate replays; changing an
existing event's content is rejected. Append a new correction event instead.

See [experiment-events.jsonl](examples/studio/experiment-events.jsonl). Each event
has a `run_id`, `event_id` and type `run`, `metric` or `artifact`. Metric values must
be finite numbers; steps are nonnegative integers. Every stored event retains its
original fields. The interface displays the latest 2,000 events and can page
backwards without loading the complete log. Repeated metric steps use the latest
received sample in the curve; original events remain available.

Log statements are distinct from observed execution. Imported runs keep
`execution_verified=false`, including a log that declares success. A run title
edited locally is not replaced by background synchronization. MLflow/W&B are
still file exchange integrations; they are not represented as live connections.

### Review protocols and question families

A protocol freezes the research question, query, inclusion/exclusion criteria,
extraction fields and candidate editions. Abstract and full-text decisions record
explicit reading confirmation, the edition revision and a reason. Full-text
screening requires an original PDF. Extracted fields require current full-text
inclusion and reviewed anchors from that edition. Changes create new receipts;
changed sources are marked stale. CSV export neutralizes spreadsheet formulas and
includes a machine-readable receipt. No model score automatically excludes papers.

Question families are reviewed descriptors of concept, required method and
conditions, with an explanation. Lexical/concept candidates are suggestions only.
Review reveals the solution first and records that disclosure. Reclassification
uses optimistic concurrency and append-only history. Same-source questions are
not automatically equivalent. Delayed transfer requires a distinct reviewed
family, the same reviewed objective, at least 24 hours and no disqualifying
assistance. Existing assistance records are not removed by family edits.

### Cross-device transfer and offline study

Project transfer is explicit encrypted file exchange, not background cloud sync.
Use a passphrase of 12–256 characters. AES-GCM authentication and Scrypt key
derivation protect the package. Limits: 200 MB per decoded project manifest,
50 MB per file, 20,000 resources. Large projects should be split deliberately.

Export covers selected project objects and their referenced library originals,
revision histories, dependency snapshots, manuscript sources, feature data,
conversations, attempts, notebook categories and reviewed question families.
Pending notebook projections travel with committed grade receipts and recover
idempotently. Imported integer IDs are remapped; URLs bind to the destination
workspace. Active turns are cancelled instead of restarted on another device.
Credentials, remote connections, memory grants and execution jobs are excluded.
Reauthorize access deliberately on the destination.

The preview compares the last exchanged baseline, current local content and the
incoming package. Every conflict needs an explicit local/incoming decision. A
concurrent change invalidates that decision. Import records each completed step
and resumes after interruption. Re-preview is required after further edits;
older generations cannot roll back a newer exchange. Deletions of shared library
objects retain tombstones and history. Numeric revisions are local to each device;
dependency correspondence uses content hashes and preserves the consumed snapshot.

Offline storage is opt-in in this browser. PDF originals and prompt-only questions
live in IndexedDB, scoped to account and workspace. The service worker only caches
the public offline shell, never API responses, credentials or answer keys. The
reader works on localhost or HTTPS. Reconnect submission checks the account and
uses a stable attempt ID, so retries do not add another grade. Offline independence
cannot be verified and is recorded as unknown; device clocks do not establish
backdated delayed-transfer evidence. Browser data clearing removes offline copies;
export the project to retain a portable backup.

### Evaluation

Reproduce the public corpus with `python scripts/prepare_mmlu_studio.py data.tar --output suite.json`; the script records source and archive hashes and makes no model calls.

Use [evaluation-suite.json](examples/studio/evaluation-suite.json) as a schema
example, not a benchmark. Each case declares domain, question family, paper IDs,
development/test split, prompt, sources and reference expectations. Families and
papers cannot cross the split. Clean/perturbed pairs share family and split.
Freezing stores a content digest. Only task and supplied sources reach the model;
references, split labels and curator metadata do not.

Automatic correctness requires both an exact accepted answer and exact source
version/locator sets. Open answers remain pending human review. Failures,
interruptions and refusals remain in the denominator. Reports separate answerable
coverage, refusal, citation precision inputs, perturbation regressions, elapsed
time, provider-reported tokens and estimated tokens. Missing prices remain null.
The evaluation loop has durable per-case leases. A crash leaves an interrupted
receipt and never silently repeats a potentially billable call. Resume proceeds
to unfinished cases; rerun a sealed suite explicitly for another attempt.

For an isolated CLI run with the existing local Codex OAuth login:

```sh
python scripts/evaluate_studio.py suite.json --home /absolute/path/to/isolated-runtime \
  --output report.json --model MODEL_ID
```

The public MMLU acceptance corpus used 45 deterministically selected questions and
45 paired variants from college computer science, high-school statistics and
machine learning. With `gpt-6.1-sol` via Codex OAuth it scored 85/90 (94.4%), with
88/90 answerable coverage, two unnecessary refusals and zero clean-to-perturbed
regressions. It used about 384 seconds of calls. All 24,254 tokens were **estimates**;
provider-measured tokens and monetary cost were unavailable. Public training-data
contamination cannot be excluded. This is not independent certification or a test
of HOI paper reading, citation localization or open-ended teaching.

## Verification and deployment

The repository now runs full-tree ESLint rather than exempting inherited errors.
`npm --prefix web run check:fast` checks generated contracts, dependency layers,
types, Node tests, component tests, lint and i18n. The Studio CI also runs the real
Chromium offline workflow and isolated domain regressions. Production route
budgets accept `DEEPTUTOR_NEXT_DIST_DIR` so the exact staged build can be checked.

Back up the stopped runtime, verify checksums and SQLite integrity, restore into a
separate location and exercise migration there. Deploy only the tested source and
production build. Keep the previous build and cold backup for rollback. Never
copy acceptance fixtures or test answers into the user's formal workspace.

Live Zotero credentials and a real SSH/GPU server were not supplied for acceptance.
Their authorization, replay, revocation and race behavior is exercised with local
fixtures; real remote service acceptance remains distinct from those tests.
