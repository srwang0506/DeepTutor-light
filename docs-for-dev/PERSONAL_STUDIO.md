# Personal learning and research Studio

[Continuity and integrations](STUDIO_REVIEW3.md) · [Handbook](README.md) · [Research](SCIENTIFIC_RESEARCH.md) · [Practice](PRACTICE.md)

This fork keeps DeepTutor's modular monolith, model registry, project grants and
durable session store. Studio connects original documents, practice, evidence,
experiments and real TeX files. A source, an interpretation and a verified result
are separate objects; a successful API call must not silently promote one into
another.

## Entry points and object identity

Today restores reading positions, pending claims, practice activities, writing
proposals and imported snapshots. Browser manuscript conflicts also appear here;
their draft stays in that browser until resolved. The library is shared within
the current account; evidence, runs, manuscripts and activities retain their
workspace scope. Existing explicit cross-project memory grants still apply.

`studio/contracts.py` defines `ObjectRef`, `ActionRequest`, `ActionReceipt` and
`ConnectorManifest`. An object reference carries owner, scope, kind, ID, revision
and optional physical locator. `/objects/<id>?revision=<n>` opens the frozen
revision and separately reports whether a newer revision exists. Permission is
checked at access time, not inherited from possession of the link.

`practice_from_selection` is the first action using this contract. It verifies
the source version, PDF hash and quote, uses only the selected source, calls the
existing model service without tools, and persists formal quiz contracts and an
activity. It checks scope again after generation. The operation has a stable
idempotency key and running/prepared/committed/failed receipts; retry does not
create another practice activity. Offline requests require a local model.
Cost is nullable when the provider cannot supply reliable accounting.

Connector capabilities are visible in Connections. File import/export is not
described as an online sync. SSH and GitHub reads produce snapshots, not proof
that repository code or experiments were executed.

## Evidence and comparisons

`studio/evidence.py` separates four checks:

| Check | Meaning |
| --- | --- |
| `anchor_valid` | The original file, physical page and optional region exist. |
| `quote_match` | The text matches inside that region, or the page when no region is supplied. |
| `visual_review` | A person checked a figure, scan or formula and recorded a review note. |
| `claim_support` | A reviewed edge describes support for a specific claim and its limitations. |

A rectangle alone cannot confirm a quote. Scans without a text layer can be
reviewed explicitly without inventing a textual match. Legacy evidence without
the new verification schema is projected as unreviewed while preserving its
prior status and immutable history.

Claim edges retain source revisions, polarity, qualifiers and review state.
Confirmed claims require reviewed support; unresolved opposing evidence cannot
be silently stored as support. Editing a claim uses optimistic revision checks.

Protocols have general, HOI detection, HOI reconstruction, trajectory and
mathematics profiles. Essential fields cannot all be marked not applicable.
Comparison distinguishes comparable, conditional, unknown and conflict. It
preserves case-sensitive metrics and units, converts only explicitly supported
compatible units, and retains original values. Missing or incompatible conditions
prevent a numeric ranking. A descriptive difference is not statistical evidence.
Plots use the same conversion factors and retain the original input snapshot and
normalization report.

## Library, discovery and retrieval

Library filters and project membership are evaluated in SQLite before keyset
paging. A page cursor includes the filter fingerprint. Matching editions are
joined to selected works without independently truncating the two lists. The
edition/work expression index uses compatible affinity so an indexed lookup is
not accidentally replaced with a full scan. Direct object reads do not depend
on list position. Nonempty symbol-only queries use a literal fallback.

Bibliography import preserves the original record and publication type.
Same-format export retains fields; conversions report fields requiring review.
Catalog-generated entries are not described as lossless imported originals.
Native RIS and CSL export require records originally imported in that format.

Crossref sorted discovery uses bounded offset paging, not the unsupported
cursor-plus-publication-sort combination. Coverage, the result cap and changing
provider snapshots remain visible. Discovery snapshots do not establish that
every matching paper was retrieved.

The study locator distinguishes exercise, equation, table, figure, section and
definition labels, including suffixes such as Table 3b. An ordinary numbered list
does not by itself identify Exercise 7. Scoped structural candidates are fused
with existing retrieval and neighboring context. This is a bounded cached label
index; a persistent ingestion-time table/OCR structure index remains future work.

## Learning exposure history

`services/session/quiz_contracts.py` persists exposure before a hint or answer is
shown. Server-side completion checks the workspace, question version and family;
starting a new attempt cannot erase exposure. Same-version exposure remains
relevant, while the current family policy uses a 24-hour window. Selection-based
families are conservatively derived from the selected source text, not claimed
to be semantic equivalence detection.

Historical assisted submissions are backfilled once into the exposure ledger.
The migration is idempotent and does not rewrite old grades. Correcting an answer
can remain positive feedback while being recorded as assisted/correction rather
than independent mastery. This policy is not yet an FSRS or delayed-transfer
mastery model.

## Manuscripts, compilation and exports

The browser keeps the actual base text/hash, local draft and current disk text.
A conflict remains a persistent editing state after its dialog closes. Autosave,
flush and manual save stay blocked until explicit resolution. Reopening the
manuscript restores that state instead of rebinding an old draft to a new hash.

Patch review flags signed numbers, units, changed comparison direction, scope
and causal language. These are review signals, not a semantic proof. File writes
and proposal receipts share the existing recovery journal; recovery can reconcile
a file that changed before the database transaction completed.

Builds freeze inputs. SyncTeX forward/inverse mapping reads the retained map and
checks source hashes; it does not claim that a stale PDF maps to edited source.
Public submission export computes the main-file dependency closure and rebuilds
it in a clean isolated directory before making the ZIP. Internal research archive
is a separate action. Rebuttal exports freeze response/source versions and list
unresolved items.

macOS uses the installed TeX toolchain under `sandbox-exec`. Linux has a restricted
bubblewrap command path but needs real platform acceptance and stronger resource
quotas before equivalent support is claimed. Unsupported platforms fail closed
for execution while retaining editing/export capabilities. Tests use synthetic
templates; a user's own template still needs its own acceptance.

## Local access and safe restoration

For local authentication-disabled mode, start API and UI together with
`scripts/study.py run` or `deeptutor start`. The launcher shares an ephemeral
`DEEPTUTOR_LOCAL_CAPABILITY` privately between them and configures explicit UI
origins. The server-side proxy injects that capability. HTTP and WebSocket calls
validate host, origin and the capability before domain authorization. The
HttpOnly SameSite cookie name is instance-specific so local previews do not
overwrite another instance's WebSocket session. The capability must never be
placed in a `NEXT_PUBLIC_` variable, bundle, URL or log.

Only health routes and bundled public UI assets are public. Remote-user mode
continues to use ordinary authentication. A standalone auth-disabled API without
the capability fails closed; advanced split-process development must explicitly
share the same private capability and allowed origins.

The SSH repository probe reads Git identity without `git status`, which can run
configured worktree filters. An unverified dirty state is reported as unknown.
SSH acceptance fixtures do not authorize access to a real server.

Stop API and workers before backup. Restore validates the complete manifest,
rejects unlisted links/special files, and copies listed files using no-follow
directory-relative opens with size/hash checks. It does not recursively follow
directories. Unsupported safe-open primitives fail closed. Before upgrades keep
the old source/build and a verified runtime snapshot; rollback must use matching
source and data, not assume a down-migration.

## Verification and capability boundaries

The `Personal Studio contracts` workflow preserves domain results keyed to the
commit SHA and checks generated API contracts, TypeScript and Node tests. Local
commands are listed in [Testing](TESTING.md); `tests/studio` contains adversarial
regressions and positive controls. Real TeX acceptance is opt-in with
`STUDIO_TEX_INTEGRATION=1` and an installed supported toolchain.

Keep provider, browser and platform acceptance separate from unit-test counts.
Zotero incremental synchronization, online tracking adapters, multi-seed
statistical inference, structured hypothesis experiments, delayed transfer
scheduling and a held-out research/learning benchmark are not implied by the
presence of their file formats or contract types.
