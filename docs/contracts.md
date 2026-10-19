# Shared contracts v1

Contract ID: `cmdl-api-v1`; storage: [`cmdl-storage-v1`](database-schema.md);
benchmark: [`deskdrop-temporal-v1`](benchmark-proposal.md). T04 specifies the interfaces;
services, migrations, models and screens remain later tickets. Version 1 is ready for
review; separate approvals from Arthur, Charly and Youssef are not recorded.

The machine-readable source is [OpenAPI 3.1](api/openapi.json). All examples in
[examples.json](api/examples.json) are **illustrative fixtures**, not trained model
outputs or measured quality results. Contract tests validate both valid and invalid
payloads and the example version links. JSON Schema checks shapes; fixture audits
check cross-field relations. Runtime and database enforcement are T06/T21. These
checks do not prove a running API or database.

## Context, time and identifiers

All user/article/session IDs are exact source strings; internal IDs are UUID strings.
Responses use UTC timestamps ending in Z. Data endpoints accept optional snapshot_id;
omitting it selects the configured active **published** snapshot, returned explicitly.
Unknown snapshot IDs return 404, malformed UUIDs return 422. The UI can use the active
snapshot by default without asking users to select internal versions.

Each response distinguishes request_time (real service time) from as_of_at (the
snapshot's exclusive historical event cutoff). The 2026 wall clock must never become
a benchmark cutoff. All catalog, history and metadata decisions use the selected
snapshot and strictly-before policy. V1 exposes no arbitrary as-of override or live
read-writing endpoint. “Current” filtering in T20 means current within that published
snapshot; a new source import requires a new published snapshot and prepared outputs.
A future live overlay is a versioned extension rather than a silent benchmark change.

Model runs, ranking sets and feature sets must match the selected snapshot. Every
item identifies its exact metadata version, feature set (nullable for popularity)
and serving model run. Quality must match that item's metadata version and snapshot;
its own quality_run_id and checks_version identify the assessment. A mismatch must
never be silently joined to a newer version. The API returns explicit unavailable
quality or rejects an inconsistent selected run; it never returns a mismatched report.

## Routes and outcomes

| Route | Success | Boundary behavior |
| --- | --- | --- |
| GET /ready | 200 Ready | 503 if DB, snapshot or recommendation/fallback artifacts cannot be read consistently |
| GET /recommend/{person_id}?limit=10 | 200 RecommendationResponse | Unknown well-formed user gets popularity fallback; no user is created |
| GET /similar/{content_id}?limit=10 | 200 SimilarResponse | 404 unknown article; 409 known but unavailable reference |
| GET /articles/{content_id} | 200 ArticleResponse | 404 unknown identity; known unavailable articles return their state and no future metadata |
| GET /quality/report | 200 QualityReportResponse | Snapshot-scoped denominator and explicit unassessed count; no run means unavailable, not perfect quality |
| GET /quality/{metadata_version_id} | 200 QualityResponse | 404 unknown version; 409 version not in selected snapshot; matching version with no completed assessment is 200 unavailable |
| POST /quality/warnings/{warning_id}/reviews | 201 ReviewDecision | Identical idempotent replay is 200; 404 unknown warning; 409 known version/run/snapshot mismatch or reused key with different payload |

All data routes also support 422 InvalidInput and 503 DependencyUnavailable. Errors
have one stable envelope: request_id, code, message, retryable and field issues.
Messages contain no stack traces, DB credentials or raw SQL. A 503 may use Retry-After;
retryable tells the UI whether retry makes sense. A non-published snapshot explicitly
selected returns 409 SnapshotNotPublished. Missing or inconsistent active selection
is 503, not an unexplained empty list. `/quality/report` must be registered before
`/quality/{metadata_version_id}` in FastAPI to avoid interpreting `report` as a UUID.

No authentication or metadata editor in v1. Reviewer labels are unverified provenance.
A readiness failure may show the reviewer screen unavailable too; missing quality
alone does not block reader recommendations.

## Ranking, fallbacks and explanation limits

limit is an integer 1..10 (default 10); no clamping. Prepared sets hold at most 50
entries per subject. Refilter by availability and prior consumption using benchmark
v1, suppress duplicates, retain service order, and reindex output ranks from 1.
Fallback is **training-signal popularity**, ordered by its recorded score and exact
lexicographic string ID on ties. It uses the same snapshot/candidate/history policy.

Use the selected personalized/similar set first. If the user is unknown, lacks five
distinct eligible history items, has no prepared ranking, or eligible prepared
candidates run out, append eligible popularity items without repeating IDs. A similar
response also excludes its reference. No similarity features yields popularity
with reason unsupported_reference. Actual TF-IDF/ALS branch fallback is recorded in
model metadata; it cannot silently switch to a different snapshot or semantic model.

Return up to limit; never pad. returned_count equals items length; short_result is
true iff returned_count < limit. Empty results with reason no_eligible_candidates are
valid 200 outcomes when the dependency chain is healthy. Missing required fallback
artifacts is 503, even if a cold-start response otherwise looks like an empty result.
The envelope lists fallback reasons; each result states source primary/popularity,
explanation code and model lineage. Explanation text describes only supported signals
(e.g. popularity, content similarity); do not invent interests or claim causal relevance.
API scores are omitted: incomparable branch scores must not invite client reranking.

## Quality dimensions and score meaning

| Dimension | Meaning | Rule examples, finalized in T16 |
| --- | --- | --- |
| completeness | Required descriptive fields are populated | Missing title/text/URL/language |
| validity | Values follow the declared field format/domain | URL syntax, supported language codes |
| consistency | Fields agree with each other | Detected text language versus source label; uncertain detection is unassessed |
| informativeness | Content supplies usable descriptive information | Short/boilerplate/repeated text indicators |

A dimension score is the fraction of **assessed, applicable rule instances that pass**,
with equal weights in v1. The per-dimension counters assessed_checks, passed_checks
and unassessed_checks expose its denominator. Score is null when assessed_checks=0;
otherwise passed_checks/assessed_checks. Applicable uncertain checks remain in
unassessed_checks and cannot be counted as passing. An overall score is the arithmetic
mean of all four dimensions only when all four have numeric scores **and** every
unassessed_checks count is zero; otherwise overall_score is null. Thus partial coverage
cannot look like a fully assessed perfect record. T16 versions exact rules, thresholds
and applicability; it does not retroactively change the meaning of an existing run.

Scores are rule-coverage summaries, not probabilities of correctness, reviewer
agreement, recommendation utility or proof that metadata is error-free. A warning
contains a stable run-local UUID, check_id, field, dimension, severity info/warning/error
and reason. One warning per failed check/field; reasons can describe multiple findings.
Warnings remain inspection signals. Review labels never rewrite computed scores.

Available quality includes run/check/version IDs, dimensions, counters and warnings.
Unavailable quality has reason not_run/pending/failed/version_mismatch, null scores
and no warnings or fabricated check version. A pending/failed run may identify its
run/check version when known. Zero warnings on an unavailable result does not mean
zero errors. Full details and item summaries use the same quality-state definition.

Collection reports count only **available catalog members** in the selected snapshot.
assessed_records + unassessed_records = catalog_records. The selected completed run's
matching results define assessed_records; partial dimension coverage remains visible.
Dimension means use only non-null scores with zero unassessed checks, with their own
denominators. Warning counts are warnings, not distinct articles. Review counts are
append-only decision events, not consensus or confirmed-error counts. No run means
assessed_records=0, null means and zero dimension denominators.

## Review persistence and errors

POST body includes client_request_id, snapshot_id, quality_run_id,
metadata_version_id, reviewer_label, decision confirm/reject and optional comment.
warning_id is in the path. The server verifies the complete tuple against the stored
warning and published snapshot, and generates review_id and created_at. Reject blank
reviewer labels, unknown decision values, extra fields or comments longer than 1000
characters. Validate the input label length (1..100 characters) and nonblank content, then
strip surrounding whitespace from reviewer labels before storing and
comparing idempotency payloads; labels are case-sensitive, not person identities.

An omitted comment is normalized to null. Warning IDs are stable within a quality
run (derive from run ID, metadata version, check ID and field); a new run gets new
warning IDs. Do not copy reviews to a different run or metadata version.

client_request_id is a UUID idempotency key. Identical semantic payload yields the
same decision row and server timestamp with HTTP 200. Any different tuple, label,
decision or comment under that key is 409 IdempotencyConflict. To change a decision,
send a new key; preserve the old row. Detail responses include all review events in
ascending (created_at, review_id) order. The latest event per warning/reviewer label
is that label's current opinion, without erasing disagreements from other labels.

## UI contract and review status

Preserve service order and all version associations. Show short/empty results,
popularity fallback and unavailable/partial quality explicitly. Disable review actions
unless a concrete available warning/run/version is selected. After a failed request,
clear stale success data, display the error, and allow retry when appropriate.

Representative examples cover normal ranking, cold start, short/empty output,
similarity, unavailable quality, a populated report, article lookup, review/replay,
version conflicts, invalid input and dependency failure. They can drive a UI adapter
before live services exist. T04 is ready for human review of these artifacts;
independent contributor approval is not inferred from passing contract tests.
