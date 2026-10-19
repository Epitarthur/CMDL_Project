# Database contract v1

Contract ID: `cmdl-storage-v1`. Implemented for PostgreSQL by the T06
[migration and publication guards](migrations.md). Ingestion is T07 and snapshots T08.
This document specifies the constraints those implementations must enforce. Benchmark semantics are
[`deskdrop-temporal-v1`](benchmark-proposal.md).

Approved by Florian on 2026-10-19 following review of this schema. This freezes
`cmdl-storage-v1` for subsequent implementation. API payload approval is tracked
separately under T04; no additional contributor signatures are claimed.

## Types and naming

Source `person_id`, `content_id` and session identifiers are **TEXT**, matching
`^-?(0|[1-9][0-9]*)$`. Never convert them through floating point. All internal entity
IDs are UUIDs. File hashes are 64 lowercase hexadecimal characters. Time columns are
TIMESTAMPTZ; application output is UTC with `Z`. Source event time and processing
`created_at` are separate: import time never establishes historic availability.
Source strings remain unchanged; normalized/experimental text gets a new version.

Fields below use `?` for nullable; all other fields are required. Enumerated states,
positive row numbers, finite scores and nonnegative counts need CHECK constraints.
Delete/update cascades must not erase provenance or historical decisions. Use RESTRICT
for referenced rows; published snapshots, versions and completed runs are immutable.

## Source and identity tables

| Table | Primary key | Required columns and links | Other constraints |
| --- | --- | --- | --- |
| source_files | sha256 | filename, bytes, dataset_ref, upstream_version, event_category, imported_at | bytes >= 0; sha256 identifies bytes, not filename; event_category article/interaction |
| source_rows | (file_sha256, row_number) | FK source_files, raw_fields JSONB, duplicate_file_sha256?, duplicate_row_number? | row_number >= 1; raw_fields retains original CSV strings; duplicate pair both null or both set |
| users | person_id | created_at | Union of observed interacting users and article authors; API user count distinguishes those populations |
| articles | content_id | created_at | Identity only; no mutable title/text columns |
| article_events | article_event_id | content_id FK articles, event_type, event_at, file_sha256, row_number | UNIQUE source-row pair; event_type CONTENT SHARED or CONTENT REMOVED |
| interactions | interaction_id | person_id FK users, content_id FK articles, session_id, event_type, event_at, file_sha256, row_number | UNIQUE source-row pair; five benchmark event types |

The two event tables reference `source_rows` by composite FK. A source-row pair may
belong to only its file's event category; T06/T07 enforce the category and never map
one row into both tables. All validated raw events, including duplicate copies and
removal-only IDs, remain stored. Interaction-only IDs, if encountered, create identity
stubs; absence of any share event still makes them unresolved for modeling. FK
existence alone never establishes metadata or eligibility.

The duplicate pointer is a composite self-FK to an earlier canonical source row,
never itself and never another duplicate. For each exact full-row group within a
file, its smallest row number is canonical. Do not deduplicate repeated user/article
pairs. Byte-identical reimports reuse the file/hash/row keys and create no new events.
Malformed file structure fails validation before transactional publication; originals
and a failure diagnostic remain available outside normalized published inputs.

## Metadata and snapshot tables

| Table | Primary key | Required columns and links | Other constraints |
| --- | --- | --- | --- |
| metadata_variants | variant_id TEXT | kind original/damage, config JSONB, config_sha256, seed?, created_at | One `original` definition; published configs immutable |
| metadata_versions | metadata_version_id | content_id, share_event_id FK article_events, variant_id FK, base_version_id?, title, text, url, lang, content_type, payload_sha256, created_at | UNIQUE (share_event_id, variant_id); UNIQUE (metadata_version_id, content_id) |
| snapshots | snapshot_id | protocol_id, as_of_at, source_manifest JSONB, transform_version, metadata_variant_id FK, status, created_at | status building/published/failed; manifest pins hashes; as_of_at is exclusive boundary |
| snapshot_articles | (snapshot_id, content_id) | snapshot FK, article FK, metadata_version_id?, availability | Composite FK (metadata_version_id, content_id); availability available/not_shared/removed/ambiguous |
| snapshot_interactions | (snapshot_id, interaction_id) | both FKs, duplicate_of_interaction_id?, exclusion_reason? | duplicate flag is separate from the temporal exclusion reason |

A metadata version is backed by a **share** event for the same content ID; a removal
row's copied title/text is not a source for article metadata. Originals have null
base_version_id; damage variants reference an original version for the same share
and content ID. Enforce these cross-table assertions with composite keys plus
transactional checks/triggers in T06/T07, not merely unrelated UUID foreign keys.
Do not infer publication dates from share timestamps.

For each snapshot, the selected variant matches every membership version's variant.
An available member requires a non-null version whose share event is strictly before
as_of_at. Other states may retain a previously visible version but are not candidates;
never expose a future version. A same-second lifecycle event is ambiguous. Conflicting
last events remain ambiguous until a later unambiguous event. Snapshot membership
records final state at that boundary; it does not replace article event history.

For snapshot interactions, duplicate copies point to their canonical interaction;
source duplicates are a separate preparation stage. Exclusion reason is null or one
of unresolved/removal_only/pre_share/ambiguous/removed, using benchmark precedence.
Eligible modeling input means canonical **and** no exclusion reason, with event_at
strictly before the fitting cutoff. Training-user minimum history is a later derived
filter; publish its population counts separately rather than deleting raw events.

## Quality and human review tables

| Table | Primary key | Required columns and links | Other constraints |
| --- | --- | --- | --- |
| quality_runs | quality_run_id | snapshot_id FK, checks_version, config JSONB, config_sha256, status, created_at, completed_at? | status pending/running/completed/failed; UNIQUE (quality_run_id, snapshot_id) |
| quality_results | (quality_run_id, metadata_version_id) | snapshot_id, four dimension scores?, overall_score?, dimension coverage JSONB | Composite FK to quality_runs and matching snapshot membership/version |
| quality_warnings | warning_id | quality_run_id, metadata_version_id, check_id, field, dimension, severity, reason | FK to quality_results; UNIQUE (quality_run_id, metadata_version_id, check_id, field) |
| review_decisions | review_id | warning_id, quality_run_id, metadata_version_id, snapshot_id, reviewer_label, decision, client_request_id, created_at, comment? | UNIQUE client_request_id; composite FK to warning's run/version and its snapshot |

Scores are finite values in [0,1] or NULL, using the definition in `contracts.md`.
A missing result row is unavailable. Failed/pending runs cannot masquerade as completed
quality. NULL never becomes 1 or 0 through COALESCE in response serialization.
A warning is a check signal; reviews do not alter computed scores or source fields.

Reviews are append-only. The exact warning/run/version/snapshot tuple is immutable.
Changing a review creates a new row and client request UUID. Same client request and
same semantic payload returns the original decision; reuse with different payload
is a conflict. Compare payload fields, not server-generated IDs/timestamps.
Latest decision per (warning_id, reviewer_label) is ordered by created_at then review_id;
retain and return history. Different reviewer labels may disagree. Labels are unverified
provenance in this local demo, never authentication or authorization.

## Features, models and prepared rankings

| Table | Primary key | Required columns and links | Other constraints |
| --- | --- | --- | --- |
| feature_sets | feature_set_id | snapshot_id FK, feature_version, artifact_uri, artifact_sha256, config JSONB, fitted_before_at, created_at | References a local artifact; no blobs in Git |
| feature_items | (feature_set_id, content_id) | metadata_version_id, snapshot_id | Composite version/content and snapshot membership FKs |
| model_runs | model_run_id | snapshot_id FK, protocol_id, model_type, feature_set_id?, quality_run_id?, status, manifest JSONB, artifact_uri?, created_at | model_type popularity/tfidf/als/fixed_hybrid/semantic/quality_hybrid |
| ranking_sets | ranking_set_id | snapshot_id FK, model_run_id FK, kind user/similar, status building/published/failed, created_at | Model snapshot must equal ranking snapshot |
| prepared_rankings | ranking_entry_id | ranking_set_id FK, snapshot_id, person_id?, reference_content_id?, content_id, metadata_version_id, rank, score, explanation_code | Exactly one subject type; finite score; rank 1..50 |

Model status is pending/running/completed/failed. Only completed runs may be selected
for serving. Hybrid branch run IDs are recorded in the manifest and must reference
completed runs with the same snapshot/protocol; record each branch’s feature set.

A user ranking uses person_id and a similar-article ranking uses reference_content_id.
Enforce unique (ranking_set, subject, rank) and (ranking_set, subject, content_id)
with partial indexes so NULL subjects do not defeat uniqueness. A similar ranking
excludes its reference item. Use a composite FK for (metadata_version_id, content_id),
and enforce membership in the ranking snapshot. Quality-dependent runs must reference
quality results for the same snapshot/version as their feature items; no cross-version
quality joins. A missing branch follows a recorded model fallback, never a silent
version substitution.

Store ranked results in score-descending, exact content-ID lexicographic ascending
order using PostgreSQL `COLLATE "C"` for the tie key. Never rerank API results by title
or rounded display scores. Rank is authoritative after filtering/reindexing.

## Composite keys required by the links above

In addition to the table keys, create explicit UNIQUE keys on:

- article_events(article_event_id, content_id).
- quality_runs(quality_run_id, snapshot_id).
- quality_warnings(warning_id, quality_run_id, metadata_version_id).
- feature_sets(feature_set_id, snapshot_id).
- model_runs(model_run_id, snapshot_id).
- ranking_sets(ranking_set_id, snapshot_id).
- snapshot_articles(snapshot_id, metadata_version_id).

Then use paired foreign keys, not merely independent ID references: quality_results
links its run and snapshot together and its metadata version to that snapshot;
review_decisions links the warning/run/version tuple and run/snapshot tuple;
feature_items links its feature set/snapshot and snapshot/version;
model_runs links feature set/snapshot and quality run/snapshot when present;
ranking_sets links model/snapshot; prepared_rankings links set/snapshot and
snapshot/version. Thus prepared_rankings also stores snapshot_id explicitly.
Any cross-row condition not expressible as an FK, such as share-event type,
cutoff timing or branch dependencies in a manifest, requires an explicit publication
check/trigger in T06; a UUID FK by itself is insufficient.

## Required access paths and publication boundaries

- Article timeline: article_events(content_id, event_at, article_event_id).
- User history: interactions(person_id, event_at, interaction_id); add content_id/event_at
  for item aggregation. Source-row unique indexes support import idempotency.
- Metadata lookup: metadata_versions(content_id, share_event_id, variant_id), plus
  snapshot_articles(snapshot_id, metadata_version_id) UNIQUE for downstream membership FKs.
- Quality detail: quality_results(metadata_version_id, quality_run_id),
  quality_warnings(quality_run_id, metadata_version_id).
- Review history: review_decisions(warning_id, reviewer_label, created_at DESC, review_id DESC).
- Prepared fetch: prepared_rankings(ranking_set_id, person_id, rank) and the equivalent
  reference_content_id partial index.

Only publish a snapshot after its manifest, memberships and exclusions are complete.
Only serve a ranking set once its model, feature/version links and ranks validate.
Only completed, version-matching quality runs are selected. Batch publication is
transactional; readers never observe half-published outputs. More than one run may
exist; API selection uses explicitly configured active run IDs, not an arbitrary
MAX(timestamp) from potentially incompatible runs. Readiness fails when those IDs
cannot be resolved consistently.


T06 makes two publication details explicit on 2026-10-19: file event category and
ranking-set status. These additive columns enforce the existing category/publication
rules; benchmark and API semantics are unchanged. Exact JSON encodings, transition
rules and validation evidence are documented in [migrations](migrations.md).


## Successful source imports (T07)

Migration 0002 adds `source_imports` with UUID `import_id`, import/protocol versions,
article/interaction file hash FKs, the original source manifest, an exclusion report
(JSONB), and processing `created_at`. The version/protocol/hash tuple is unique;
rows are append-only. It commits with both files' normalized events and metadata so
replays can verify completion and regenerate exports. This additive audit table
preserves the approved storage/benchmark semantics. See [ingestion](ingestion.md).
