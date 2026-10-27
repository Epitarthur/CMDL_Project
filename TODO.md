# Project TODO and implementation tickets

Status: T01–T03 completed on 2026-10-19 following Florian’s explicit approval and closure instruction. Benchmark v1 is frozen, publisher version/license and current slide counts are verified, and tooling/CI pass. T04’s schema and API contract are approved and complete; T05’s Docker environment is implemented and locally verified; T06 migrations and database guards are implemented and verified; T07 ingestion and its exclusion audit are implemented and verified; T08 published snapshots and temporal inputs are implemented and verified; T09 local run tracking is implemented; T10 onward remain planned.

Tickets are local planning records, not GitHub Issues. Owners are defaults. Dependencies use ticket numbers; work within a phase may proceed in parallel when dependencies permit. Update status and link implementation commits/PRs and evidence as work completes.

## Scope and completion rules

- Use Docker Compose, PostgreSQL, Python, FastAPI and Streamlit. No authentication initially.
- Preserve tracked raw CSVs. Keep generated outputs, secrets and models outside Git.
- Complete baseline models before semantic and quality-dependent extensions.
- Two-Tower, LightGCN and a metadata correction editor are outside initial scope.
- A ticket is done when implementation, relevant validation and documentation are reviewed. Keep tests focused on observable behavior, contracts, data integrity and leakage risks.
- Resolve methodological decisions before dependent implementation; results need not show an improvement.

## Foundation

### [x] T01 — Agree benchmark and data semantics

**Completed:** `deskdrop-temporal-v1`, including cutoffs, signals, eligibility, chronological examples and change control, approved by Florian on 2026-10-19. See `docs/benchmark-proposal.md`; no additional team signatures are claimed.

**Owner:** Florian + Arthur + Charly  
**Depends on:** None

**Implement / produce:** Document UTC cutoffs after inspecting event distribution; consumption and relevance events; event weights and repeat aggregation; train-only minimum-history filtering; removal/re-share rules; duplicate and unresolved-ID policy; tie-breaking and retraining protocol.

**Validate / acceptance:** Use small chronological examples to demonstrate candidate eligibility, pre-share exclusion, removal/re-share behavior and no future leakage. Approval is recorded before model work; Florian approved v1 on 2026-10-19.

### [x] T02 — Verify raw dataset and provenance

**Completed:** Verified both CSVs against publisher version 5, documented ODbL/DbCL attribution, reconciled overlapping anomalies and current final Part 2 slide claims, and retained reproducible evidence. See `docs/data-profile.md`, `docs/dataset-provenance.md` and `docs/evidence/`. Original CSV bytes unchanged.

**Owner:** Florian  
**Depends on:** None

**Implement / produce:** Verify checksums, CSV schema, exact string IDs, event types, counts, timestamp range, missing fields and join anomalies. Confirm dataset version and redistribution terms; document exclusions without changing raw files.

**Validate / acceptance:** Reproduce profile from both committed CSVs; reconcile slide counts and overlapping anomaly groups; fail clearly on a changed checksum or malformed input.

### [x] T03 — Establish Python tooling and CI

**Completed:** Python 3.12.13, uv lockfile, package/CLI, Ruff, pytest and GitHub Actions implemented and validated locally and in hosted CI. Florian approved closure on 2026-10-19. Evidence: `docs/development.md`; foundation commit `f0aa8fc`, [passing CI](https://github.com/Epitarthur/CMDL_Project/actions/runs/39199043980).

**Owner:** Florian + team  
**Depends on:** None

**Implement / produce:** Select and pin Python/dependencies, package src/cmdl, add formatting/linting and test commands, lock dependencies, and add GitHub Actions. Keep secrets and generated outputs excluded.

**Validate / acceptance:** Fresh environment installs the package; lint and meaningful tests run in CI; generated outputs and local secrets remain ignored.

### [x] T04 — Freeze schema and service payloads

**Completed (2026-10-19):** Florian approved both `cmdl-storage-v1` and `cmdl-api-v1`, including representative payloads. The database specification, seven API routes, quality score semantics, fallback behavior, version matching, review idempotency and errors are documented in `docs/database-schema.md`, `docs/contracts.md` and `docs/api/openapi.json`. All 23 examples and cross-field checks pass; 64 tests and [hosted CI](https://github.com/Epitarthur/CMDL_Project/actions/runs/39216881264) passed for implementation commit `7f47bdc`. Future changes are allowed with versioned contract/example updates. No additional contributor signatures or running services/migrations are claimed.

**Owner:** Florian + Arthur + Charly + Youssef  
**Depends on:** T01, T02

**Implement / produce:** Specify database entities, version links and API request/response examples including string IDs, unavailable quality, fallbacks, short results, errors and review decisions. Define quality dimensions and score meaning.

**Validate / acceptance:** Team reviews representative examples; contracts preserve large IDs, ordered rankings and matching metadata/check/model/snapshot versions.

### [x] T05 — Create Docker Compose development environment

**Owner:** Florian + Youssef  
**Depends on:** T03, T04

**Implement / produce:** Add database, API and Streamlit images/services, persistent database volume, health/readiness checks and environment example. Add explicit batch job commands; document startup and reset. No auth, scheduler or Redis initially.

**Validate / acceptance:** On a clean laptop setup starts successfully; UI reaches API and database; data persists after restart; reset behavior is explicit and repeatable.

**Evidence (2026-10-19):** Fresh isolated Compose volume/image setup on Florian’s
Docker engine passed UI → API → SQL connectivity, honest 503 application readiness,
restart/recreation persistence and volume-reset checks. Explicit source-profile job
passed. 66 tests and Ruff pass. The smoke check is included in CI; a separate
teammate laptop run is not claimed. See [environment guide](docs/compose.md).

## Data and reproducibility

### [x] T06 — Implement database migrations

**Owner:** Florian  
**Depends on:** T04, T05

**Implement / produce:** Create users, articles, article events, interactions, snapshots, metadata versions, quality runs/warnings, review decisions, model runs and rankings with foreign keys and indexes. Preserve source provenance.

**Validate / acceptance:** Migrations work on an empty database; invalid references are rejected; original versions survive later versions; timestamps and string identifiers round-trip.

**Evidence (2026-10-19):** 20 application tables plus a checksummed migration ledger,
composite foreign keys, indexes and transactional publication/history guards are
implemented. 49 real PostgreSQL checks pass; full suite 115 passed. The runtime image
passed initial/repeated migration, persistence and reset/reinstallation checks.
Migration applied to the local development database; no raw ingestion performed.
See [migration guide](docs/migrations.md) and [validation](docs/development.md).


### [x] T07 — Build idempotent CSV ingestion

**Owner:** Florian  
**Depends on:** T01, T02, T06

**Implement / produce:** Validate and import raw rows; separate metadata from article events; identify source rows with file checksum and row number; apply documented source duplicate handling; publish exclusion report.

**Validate / acceptance:** Import twice without doubling records; test malformed rows, repeated rows, removal-only IDs and pre-share interactions; reconcile retained and excluded totals.

**Evidence (2026-10-19):** Transactional importer, append-only import ledger and
replayable exclusion report implemented. Both source files imported into the local
development database and replayed without additional records. All 75,434 source rows
are retained; 72,312 interactions partition into 11 exact copies, 166 temporal
exclusions and 72,135 canonical eligible rows before cutoff/history filters.
134 tests pass; Docker verifies packaged import/replay, persistence and reset.
See [ingestion guide](docs/ingestion.md) and [validation](docs/development.md).


### [x] T08 — Publish snapshots and temporal eligibility

**Owner:** Florian + Arthur  
**Depends on:** T07

**Implement / produce:** Create reproducible snapshots, metadata-as-of lookup, availability and previously consumed filtering. Implement shared candidate policy and train/validation/test partitions.

**Validate / acceptance:** Boundary tests cover events at cutoffs, removals, re-shares, unseen items and metadata versions; no model receives future histories or a different candidate policy.

**Evidence (2026-10-19):** Atomic, deterministic validation/test snapshots published
and replayed in the development database. Shared frozen model views enforce earlier
metadata/history, fixed catalogs and prior-consumption filtering. Half-open raw
splits reconcile to 66,508 / 2,895 / 2,909 events. Catalogs contain 2,788 / 2,902
available items; canonical fitting histories contain 66,332 / 69,226 events.
Snapshot/variant/holdout checks and Docker publication/persistence/reset pass.
See [snapshot guide](docs/snapshots.md) and [validation](docs/development.md).


### [x] T09 — Implement run manifests and artifact tracking

**Owner:** Arthur + Florian  
**Depends on:** T03, T08

**Implement / produce:** Record code revision, dependencies, source checksums, transformations, versions, cutoffs, parameters and seeds. Define artifact paths and choose lightweight tracking or MLflow.

**Validate / acceptance:** Same configuration reproduces inputs and rankings within documented numeric tolerance; changed inputs produce distinguishable run identities; artifacts remain outside Git.

**Evidence (2026-10-27):** Content-addressed local manifests pin clean Git revision,
locked/installed dependencies, published snapshot descriptors, model/transform versions,
parameters, seeds and numeric tolerance. Atomic artifact capture/replay verifies byte
checksums and distinguishes changed inputs/outputs. See [run tracking](docs/runs.md).
Validation uses illustrative rankings; trained-model reproducibility remains with the
model tickets. No serving model publication or hosted CI result is claimed.

## Recommendation baseline

### [ ] T10 — Build implicit training signals

**Owner:** Arthur  
**Depends on:** T01, T08

**Implement / produce:** Aggregate permitted training events into preference/confidence matrices; apply training-only user/item filters and record exclusions. Define cold-start populations.

**Validate / acceptance:** Test repeated events, weights, sparse histories and unseen users/items; validation/test activity never affects filters or confidence.

### [ ] T11 — Implement popularity baseline and fallback

**Owner:** Arthur  
**Depends on:** T08, T10

**Implement / produce:** Rank eligible articles by training signal with deterministic ties; expose fallback reason and supported explanations.

**Validate / acceptance:** Known examples yield expected ordering; removed/read articles never appear; empty catalogs and new users produce valid results.

### [ ] T12 — Implement TF-IDF content baseline

**Owner:** Arthur  
**Depends on:** T08, T10

**Implement / produce:** Specify title/text weighting, language handling, preprocessing and user-profile construction. Fit vocabulary on permitted data and score unseen candidates.

**Validate / acceptance:** Test empty/short content and users without usable history; verify deterministic scores and no future corpus fitting; record feature versions.

### [ ] T13 — Implement implicit ALS baseline

**Owner:** Arthur  
**Depends on:** T10, T09

**Implement / produce:** Train latent factors from implicit confidence with pinned seeds and validation tuning. Route unsupported users/items to documented fallbacks.

**Validate / acceptance:** Check matrix mapping and confidence semantics; no consumed/ineligible outputs; cold-start behavior and reproducibility are verified.

### [ ] T14 — Implement fixed hybrid ranking

**Owner:** Arthur  
**Depends on:** T11, T12, T13

**Implement / produce:** Document score normalization, blend ALS and TF-IDF, tune blend on validation only and support missing-branch fallbacks.

**Validate / acceptance:** Test normalization boundaries, equal scores and absent branches; compare against baselines using identical evaluation inputs.

### [ ] T15 — Build offline ranking evaluation

**Owner:** Arthur  
**Depends on:** T08, T09, T11, T12, T13, T14

**Implement / produce:** Implement Recall@10, NDCG@10, catalog coverage, language slices and paired user outputs. Specify relevant-positive and coverage denominators and users without eligible positives.

**Validate / acceptance:** Hand-calculated examples verify metric values; all models use the same eligible pool and test users; report exclusions and cold-start slices.

## Metadata quality and human review

### [ ] T16 — Define quality checks and annotation protocol

**Owner:** Charly + Meiirkhan  
**Depends on:** T02, T04

**Implement / produce:** Specify completeness, validity, consistency and informativeness checks; distinguish warnings from errors. Set language/URL/duplicate/text rules, dimension scores and aggregation. Plan 200 records, two independent reviewers, calibration/held-out split and adjudication.

**Validate / acceptance:** Review examples for each rule and uncertain case; sample includes unflagged records so recall is assessable; document sampling limits and avoid score thresholds tuned on held-out labels.

### [ ] T17 — Implement versioned quality evaluator

**Owner:** Charly  
**Depends on:** T08, T16

**Implement / produce:** Generate field-level warnings with stable IDs, reasons, check versions and dimension scores. Include missing values, URL syntax, language mismatch, duplicate indicators and unusual text. Keep external URL reachability optional.

**Validate / acceptance:** Use controlled valid/invalid examples and uncertain language cases; repeated runs are reproducible; warnings attach to the correct version; missing reports never imply good quality.

### [ ] T18 — Persist review decisions

**Owner:** Florian + Charly  
**Depends on:** T06, T16, T17

**Implement / produce:** Store confirm/reject decisions with reviewer label, warning ID, record version and UTC time. Define repeat-decision/history rules. No metadata editor or authentication.

**Validate / acceptance:** Decisions survive restart; unknown warnings and mismatched versions are rejected; source fields stay unchanged and decision history stays traceable.

### [ ] T19 — Validate quality flags with reviewers

**Owner:** Charly + Meiirkhan  
**Depends on:** T16, T17, T18

**Implement / produce:** Collect independent labels, retain disagreements/adjudication, freeze checks after calibration and measure held-out agreement and per-check precision/recall where supported.

**Validate / acceptance:** Verify sample separation, label completeness and denominators; report uncertainty, sampling bias and failure cases; do not equate all warnings with proven errors.

## Service and interface integration

### [ ] T20 — Prepare rankings and online fallback

**Owner:** Arthur + Florian  
**Depends on:** T09, T14

**Implement / produce:** Precompute 50 candidates per user and store ranking/model/snapshot metadata. On request filter current reads and availability; use eligible fallback and return up to ten.

**Validate / acceptance:** Test stale candidates, 50 exhausted candidates, unavailable articles and tiny catalogs; never pad with ineligible items; retain traceable explanations and versions.

### [ ] T21 — Implement FastAPI services

**Owner:** Florian  
**Depends on:** T04, T17, T18, T20

**Implement / produce:** Implement recommendation, similar-article, article lookup, quality detail/report and review-submission contracts. Validate limits and IDs, return explicit errors and readiness state.

**Validate / acceptance:** API contract tests cover success, unknown IDs, malformed input, unavailable quality, dependency failure, short lists and review persistence. IDs remain exact strings.

### [ ] T22 — Build Streamlit reader screen

**Owner:** Youssef  
**Depends on:** 04, 05; live integration after 21

**Implement / produce:** Begin with explicit example responses, then wire the same adapter to API. Select user, request articles, display service order/explanations and inspect original record plus matching quality report.

**Validate / acceptance:** Check loading, new-user fallback, short/empty results, missing quality, changed record versions and service failure/retry; no stale success after a failure.

### [ ] T23 — Build Streamlit reviewer screen

**Owner:** Youssef + Charly  
**Depends on:** 04, 05; live integration after 18, 21

**Implement / produce:** Display collection summaries and field warnings with reasons; inspect matching metadata and confirm/reject warnings with a reviewer label. Show persisted decisions separately.

**Validate / acceptance:** Verify warning/record/version match, saved decision visibility after refresh, error recovery and unchanged original records.

### [ ] T24 — Verify complete integrated workflows

**Owner:** Youssef + team  
**Depends on:** T21, T22, T23

**Implement / produce:** Create repeatable integration scenarios with fixed inputs and retained API/test evidence for reader, inspection and review flows.

**Validate / acceptance:** One request returns correct order and versions; no unavailable/read items; review survives restart; failed request then retry recovers; missing quality remains visibly unavailable.

## Research extensions and experiments

### [ ] T25 — Add multilingual semantic content branch

**Owner:** Arthur  
**Depends on:** T09, T12, T15

**Implement / produce:** Choose/pin multilingual embedding model, chunking and pooling strategy; handle English/Portuguese and empty text; compare semantic content and its fixed ALS hybrid with TF-IDF alternatives. Add FAISS only if useful.

**Validate / acceptance:** Verify permitted corpus timing, feature/version mapping, deterministic processing and cold-start behavior; evaluate all variants under the frozen protocol.

### [ ] T26 — Build controlled metadata damage generator

**Owner:** Charly + Arthur  
**Depends on:** T08, T09, T17

**Implement / produce:** Version title removal, text shortening and language-label alteration at 0%, 10%, 30% and 50%; freeze exact severity rules, sampled records and seeds. Preserve original files and interactions.

**Validate / acceptance:** Zero damage matches original; rates and changed fields are correct; same seed reproduces variants; interactions, user set and eligibility stay fixed; rebuild affected features/checks.

### [ ] T27 — Implement quality-dependent hybrid

**Owner:** Arthur + Charly  
**Depends on:** T14, T17, T25

**Implement / produce:** Specify how version-matched content quality changes branch weight, with missing-quality behavior. Tune on validation only and compare each variant to its matching fixed hybrid.

**Validate / acceptance:** Test quality extremes, unavailable quality and missing branches; quality and feature versions agree; avoid using held-out labels or test results to choose weights.

### [ ] T28 — Run model and metadata-impact experiments

**Owner:** Arthur + Charly + Meiirkhan  
**Depends on:** T15, T19, T25, T26, T27

**Implement / produce:** Compare clean models, damage type/severity and fixed versus quality-dependent blending. Retain manifests, per-user differences, coverage, language slices and limitations.

**Validate / acceptance:** Verify unchanged controls and comparable populations; reproduce selected runs; report neutral/negative outcomes honestly and separate flag validity from recommendation performance.

### [ ] T29 — Measure serving performance

**Owner:** Florian + Youssef  
**Depends on:** T20, T21, T24

**Implement / produce:** Define hardware, warmed/cold behavior, timing boundary and request load for the proposed under-300-ms API target; measure latency distribution and query bottlenecks.

**Validate / acceptance:** Retain repeatable measurements and percentile results; test fallback path too; optimize only measured bottlenecks before considering Redis or other services.

## Delivery

### [ ] T30 — Package reproducible demo and reset

**Owner:** Youssef + Florian  
**Depends on:** T24, T28, T29

**Implement / produce:** Document clean checkout, Compose startup, data import, preparation/training, readiness checks, demo user/record choices and reset/rebuild commands. Distinguish illustrative fixtures from real results.

**Validate / acceptance:** A teammate follows the guide on a fresh environment; produces rankings and quality reports; reviews a warning; demonstrates failure/retry and reruns reset without editing raw data.

### [ ] T31 — Finalize research report and presentation

**Owner:** Meiirkhan + all  
**Depends on:** T19, T28, T29, T30

**Implement / produce:** Align five parts with implemented scope, source-backed findings, actual dataset counts, model/quality evaluation, limitations and team contributions. Set calendar milestones and confirm course submission requirements.

**Validate / acceptance:** All reported numbers trace to retained runs; no planned feature is presented as completed; rehearse the integrated demo and verify submission contents.

## Integration gates

- **Gate A:** T01–T09 establish verified data, contracts and reproducible inputs.
- **Gate B:** T10–T19 establish comparable baselines and validated quality checks.
- **Gate C:** T20–T24 demonstrate one complete reader flow and one persistent review.
- **Gate D:** T25–T29 produce controlled research results and serving measurements.
- **Gate E:** T30–T31 make the project reproducible and ready to submit.

Planning references: [architecture](docs/architecture.md), [contracts](docs/contracts.md), [evaluation](docs/evaluation.md), [delivery](docs/delivery.md), [open decisions](docs/decisions.md).
