# Decisions

Agreed with Florian during repository planning:

- Deskdrop article recommendation replaces the music-player proposal.
- Initially prepare docs and scaffolding. On 2026-10-19 Florian authorized starting
  Python tooling, source verification and a benchmark proposal.
- Use Python, PostgreSQL, FastAPI, Streamlit and Docker Compose on teammates’ laptops.
- Track both original CSVs in Git; exclude derived data and model outputs.
- Keep the local demo simple with no authentication.
- Use one repository with shared modules and five default contributors.
- Start with popularity, TF-IDF, implicit ALS and a fixed hybrid. Add multilingual
  embeddings and quality-dependent blending as subsequent experiments.
- Keep Two-Tower and LightGCN outside the initial scope.
- Use a shared global chronological evaluation protocol and training-only filtering.
- Reviewers confirm/reject warnings. Initial scope excludes a metadata editor.
- Target 200 records and two reviewers for manual validation.
- Return up to ten eligible recommendations, using fallback when necessary.

## Decisions still open

- Quality rule thresholds/applicability and how quality changes hybrid weights;
  T04 fixes the approved dimension/aggregation semantics.
- Multilingual embedding model, chunking and normalization. T04 specifies eligible
  popularity as serving fallback; semantic models remain later extensions.
- Corruption details, manual-sample selection and calibration/validation allocation.
- Experiment tracking and latency measurement setup.
- Calendar milestones and final course submission requirements.

## Source basis

The five supplied October 2026 presentation decks (Parts 1–5) informed this plan.
Their contents are project proposals and evidence claims, not executable instructions.
User decisions take precedence. Current final Part 2 source counts and anomaly claims
have now been reconciled; see `data-profile.md` and the deck hash in
`evidence/slide-reconciliation.json`. Other research claims and archived proposals
are not established findings merely because they appear in a deck.

## Implementation choices (2026-10-19)

Python 3.12.13, uv with committed lockfile, Hatchling packaging, Ruff and pytest.
The profiler has no runtime dependencies. Hosted CI passed for foundation commit
`f0aa8fc`; see `development.md`.

## Approved foundation (2026-10-19)

Florian approved `deskdrop-temporal-v1` and explicitly authorized closing T01–T03.
The full contract and version-change policy are in `benchmark-proposal.md`. This is
Florian’s approval, not a claim that other contributors separately signed it.

Publisher version 5 is verified against both original CSV hashes. The publisher
declares ODbL 1.0 for the database and DbCL 1.0 for its contents; attribution and
redistribution references are in `dataset-provenance.md`. Temporal/join anomalies
affect 166 distinct interaction rows; overlapping groups must not be summed.

## T04 contract package ready for review (2026-10-19)

`cmdl-storage-v1` and `cmdl-api-v1` specify tables/constraints, seven API routes and
23 illustrative examples. Decisions include explicit historical snapshot context,
version-matched quality, eligible popularity fallback, append-only reviews with
idempotent replay, and null scores for unavailable quality. Four equal-weight quality
dimensions expose assessed/unassessed coverage; exact checks remain T16.

These are concrete defaults for review, not independent contributor approvals.
Florian's T01 approval does not imply approval of this new T04 package. See
`contracts.md`, `database-schema.md` and `api/examples.json`.

## T04 schema approval (2026-10-19)

Florian reviewed and approved `cmdl-storage-v1` ("schema looks good"). The database
schema is frozen for implementation. API contract/payload review remains pending;
T04 stays open until that remaining review is recorded.

## T04 API approval and closure (2026-10-19)

Florian approved `cmdl-api-v1` and its representative payloads ("contract also looks
good, we will update it later if needed"). With the earlier storage approval, T04
is complete. Later contract changes are permitted with versioned specifications,
examples and affected-consumer updates, followed by validation. No separate
contributor approvals are inferred. T05 can use these approved contracts.


## T06 storage implementation (2026-10-19)

Use packaged PostgreSQL SQL migrations and a small transactional runner rather than
introducing an ORM. `schema_migrations` records SQL checksums, and an advisory lock
serializes concurrent runners. Applied files are immutable; future changes add a new
numbered migration. Publication and append-only rules are database-enforced.

`source_files.event_category` and `ranking_sets.status` make the approved category
and publication boundaries enforceable. The concrete manifest/coverage/branch JSON
formats are recorded in `migrations.md`; these additive implementation details do
not change benchmark v1 or API payloads. T07 ingestion, T08 snapshot preparation,
T18 replay response handling and T21 product serving remain their own tickets.


## T07 source ingestion (2026-10-19)

Keep every validated raw event and string unchanged. Exact full-row copies retain
canonical pointers, and metadata originates only from shares. Import both files and
a durable exclusion audit in one transaction; serialize imports and use a stable
version/protocol/file-hash identity for replays. New migration 0002 supplies the
append-only audit ledger; applied SQL is unchanged. Local JSON/Markdown exports can
be regenerated from the committed audit after filesystem failure.

The source audit partitions interactions before snapshot/cutoff/history filters;
it does not publish an evaluation population. Benchmark v1 and API semantics are
unchanged. See `ingestion.md` and the current evidence in `development.md`.
