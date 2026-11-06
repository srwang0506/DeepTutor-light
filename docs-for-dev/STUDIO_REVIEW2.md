# Studio integrity review — 1274880 follow-up

This release retains the restored interface and the modular monolith. It addresses the reproduced integrity defects in the 6 November review. It does not certify all future product work as complete.

## Contracts

- `ReadSet` captures selected records, ancestor records, revisions, current staleness, locators and rule versions in one SQLite read transaction. Callers receive copies. Figures/tables compare and render this same snapshot, and reject publication if an observation changed. Practice and writing retain the consumed revisions. Historical artifacts are not rewritten.
- `ImportReceipt` is a project-scoped immutable event, indexed independently of editable records. Replays return the original object, even after a title edit. A reused event key with different content is rejected. Bibliography enrichment retains originals, citation aliases, field provenance and conflicts; nonempty curated fields survive.
- Prompt-only learning views do not return reference answers or model metadata. Reference disclosure is recorded before the endpoint releases the reference. A committed feedback receipt records disclosure atomically, after classifying the attempt at its submission time. Subsequent attempts retain that assistance history. Same-paragraph questions no longer share an exposure identity merely because of their source.
- Receipts distinguish independent, assisted, correction, delayed transfer and historical unknown evidence. Delayed transfer requires a distinct question family, a reviewed objective and a delay of at least 24 hours. Source-only objective identifiers do not establish conceptual transfer. Generated question families currently use exact prompt/answer identity, not an independently validated semantic-equivalence model.
- Submission plans explain every required file. Submission defaults to the main file and literal dependencies; extras require selection. Dynamic expressions require acknowledgment and a clean compilation. Internal archives remain a separate explicit choice. Every build executor, including synchronous submission validation, owns the same OS lease before changing queued/running/terminal state.
- A prepared proposal journal retains the original proposal identity. Selecting the prepared bytes completes its original acceptance receipt and dependencies. Invalid journals are preserved without interpreting their contents.

## Trust and operating boundaries

A run's declared status is separate from execution evidence. Local manifests, CSV/JSON, tracking exports and SSH/GitHub snapshots are imported statements; none proves the system observed a process. `executed` observations and confirmed claims require a successful run with an execution witness. Legacy runs do not gain a witness during migration.

Protocol field states are separate from values (`known`, `unknown`, `not_applicable`, `conflict`). Missing-value sentinels are case-insensitive; scientific units and metric symbols keep their case. Descriptive differences are not significance tests.

The local browser receives an HttpOnly session derived from, and distinct from, the backend capability. Only a launch link bootstraps it. Host/Origin headers alone cannot make the UI proxy attach its backend capability. The redirect strips its launch token and preserves the requested loopback host. Generic launcher readiness uses `/health/ready`; a previous frontend cannot be silently reused with a newly generated backend token. OAuth callbacks retain their existing state validation.

TeX distribution recognition uses explicit distribution directories and runtime mounts. Filesystem-root or home-directory allowlists are rejected. SSH file reads pin directory descriptors and reject symlink replacements in intermediate components. A permission change during transport prevents persisting the downloaded bytes.

## Regressions and validation

`tests/studio/test_second_review.py` contains executable counterexamples and positive controls for concurrent rendering, immutable consumed versions, rotated PDF rectangles, invalid reading locators, bibliography enrichment, edited imports, minimal ZIP contents, suffixless dependencies, dynamic dependencies, proposal recovery, execution leases, TeX mounts, SSH symlink races/revocation, scientific search terms and protocol states.

The learning tests additionally cover prompt-only endpoints, feedback/retry classification, same-source distinct questions, legacy unknown receipts, progress-derived completion and delayed-transfer prerequisites. Browser component tests verify that a redacted question cannot display a reference until the disclosure endpoint returns. Proxy tests verify launch-cookie separation and anonymous requests. Existing 10,000-item library/index guards remain intact.

Commands (use an isolated `DEEPTUTOR_HOME` initialized with `init_user_directories()`):

```sh
python -m pytest -q tests/studio tests/learning/test_study_quiz.py \
  tests/learning/test_study_operations.py tests/services/session/test_sqlite_store.py \
  tests/runtime/test_launcher.py tests/runtime/test_launcher_lifecycle.py
# On macOS with TinyTeX, enable the real sandbox compile test:
STUDIO_TEX_INTEGRATION=1 python -m pytest -q tests/studio/test_compile_integration.py
python scripts/export_frontend_contracts.py --check
npm --prefix web run contracts:check
npm --prefix web run typecheck
npm --prefix web run test:node
npm --prefix web run test:unit
npm --prefix web run build
```

Full-tree ESLint currently has inherited errors outside this change; changed-file errors are compared against 1274880 rather than represented as a clean repository-wide lint run. Tests with fixed model responses validate persistence and trust boundaries, not actual model quality. SSH acceptance uses local simulation, not a user's server. macOS native TeX was exercised; Linux mount construction is tested without claiming a real Linux compilation.

## Release discipline and remaining product work

Restore a verified backup into a separate runtime and exercise the database upgrades before stopping the formal service. Then take a fresh cold backup, deploy the tested source/build and open its new launch link. Never restore test-generated answers or fixtures into the user's formal database. Keep the old build and database backup available for rollback.

The broader review remains a roadmap: a real Zotero incremental connector (cursors, tombstones, retries/revocation), deep live experiment tracking, complete review/screening workflows, semantic question-family validation, cross-device/offline synchronization and multi-domain acceptance are not implied by these fixes. The proposed 90–120-task blind evaluation with family/paper isolation has not been replaced by a large count of synthetic unit tests; it still needs an independently assembled task corpus, held-out inputs and measured model cost/coverage/refusal. Public/private real material and genuine remote services must be evaluated separately from the deterministic regressions above.
