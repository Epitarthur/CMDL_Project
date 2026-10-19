# Benchmark v1 — approved

Protocol ID: `deskdrop-temporal-v1`. Approved by Florian on 2026-10-19 in this
project chat, with explicit authorization to close T01–T03. This records Florian’s
approval; it does not claim separate approvals from Arthur or Charly.

This is the agreed contract for subsequent ingestion, snapshots and model evaluation.
T07/T08 now implement ingestion, snapshots and temporal model inputs.
Signals and ranking evaluation remain T10/T15. The filename is kept
stable for existing links. Source findings are in `data-profile.md`.

## Time periods

Use UTC half-open intervals:

- Training: events before 2017-01-01T00:00:00Z.
- Validation: [2017-01-01T00:00:00Z, 2017-02-01T00:00:00Z).
- Test: [2017-02-01T00:00:00Z, 2017-03-01T00:00:00Z).

Evaluate one request per user at the start of each holdout period. The candidate
catalog and consumed-item filter use only information strictly before that request.
An article shared during the holdout period is outside the fixed candidate pool;
report those positives separately rather than silently adding future candidates.
A removal before the request makes an article unavailable. A removal after the
request does not retroactively invalidate the request's ranking.

Tune parameters on validation only. Freeze parameters, then retrain on all permitted
pre-test data before the test request. Recompute minimum-history filters from that
retraining history, not from test activity. Report changing evaluation populations.
Never tune decisions or hyperparameters from the final test outcome.

## Event and record handling

- Preserve all raw rows and identify them by file checksum plus 1-based data-row
  number (CSV records excluding the header, not physical lines).
- Treat all five supported interaction types as consumption and relevance signals.
  Deduplicate exact full-row copies for modeling, retaining their source lineage.
- Exclude unresolved IDs, removal-only IDs, pre-share interactions and interactions
  when the article is unavailable. Record mutually exclusive exclusion reasons in
  this precedence order: unresolved, removal-only, pre-share, ambiguous/equal-time,
  removed. Publish counts before and after each step.
- Apply article lifecycle changes strictly after their timestamp: at the same second
  as a lifecycle event, conservatively omit the article/interaction because order is
  unknown. Conflicting events at one timestamp make availability unknown until a
  later unambiguous lifecycle event. A later share restores availability.
- Never recover unavailable historical metadata from a later share. Metadata must
  come from a share strictly before the relevant interaction or request.
- Event weights: VIEW=1, LIKE=2, BOOKMARK=3, FOLLOW=4, COMMENT CREATED=5.
  After exact duplicate handling, sum weights per user/article. Use a monotone
  `log1p(sum)` strength; ALS confidence scaling is a later validation parameter.
  These weights are benchmark choices, not measured user preferences.
- Apply the minimum of five distinct eligible articles per training user
  using only permitted training history. Keep low-history/new users as separately
  reported fallback slices. Do not derive filters from full-source interaction counts.
- One relevant positive per distinct eligible user/article pair in the holdout;
  repeated events do not increase the Recall/NDCG denominator.
- Rank by descending model score, then lexicographic exact string content ID.
  All models use the same catalog and consumption policy. Never cast IDs to floats.
- Report users with no eligible positives separately; exclude them from averaged
  ranking metrics and publish the denominator. Define coverage as the union of
  recommended IDs divided by the fixed request-time eligible catalog.

## Accepted chronological examples

Suppose A is shared at t=10, removed at t=20 and shared again at t=30.

| Event/request | Expected result |
| --- | --- |
| Interaction with A at t=9 | Excluded; later metadata is unavailable. |
| Interaction/request at t=10 | A omitted; within-second order unknown. |
| Request at t=15, no previous consumption | A eligible with t=10 metadata. |
| Request at t=15, A consumed at t=12 | A excluded from that user's candidates. |
| Request at t=20 or t=25 | A omitted at removal time and unavailable afterwards. |
| Request at t=30 | A omitted at the exact re-share timestamp. |
| Request at t=31 | A available with re-share metadata; prior consumption still applies. |
| Article B first shared after the fixed evaluation request | B excluded from that request's catalog and relevance denominator. |
| Removal-only article C | C never becomes a candidate from this export. |

Boundary: an interaction exactly at 2017-01-01T00:00:00Z belongs to validation,
never training or the request-time consumption history. Its article must already
be available strictly before the request to count as an eligible positive.

## Versioning and change control

All downstream run manifests must record `deskdrop-temporal-v1`, the source checksums,
and the configuration/code revision. A methodological change creates a new protocol
version with a dated reason and approver. Preserve earlier rules and results; do not
compare scores across protocol versions as evidence of a model improvement.

Rerun affected preparation, training, rankings and metrics when weights, cutoffs,
filters, relevance, eligibility or retraining rules change. Pure wording corrections
that do not change behavior may retain the ID and must be noted. Changes influenced
by observed test results must be disclosed; reserve a fresh holdout where feasible.

T08 executes these chronological examples in temporal-policy and real PostgreSQL
snapshot tests. It publishes fixed request-time catalogs and strictly earlier model
inputs; recommender training and ranking metrics remain later tickets. See
[snapshot preparation and shared model inputs](snapshots.md).
