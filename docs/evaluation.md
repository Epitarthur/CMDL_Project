# Evaluation plan

## Models and sequence

First implement popularity, TF-IDF content similarity, implicit ALS and a fixed
hybrid of collaborative and TF-IDF scores. Normalize scores using an explicitly
documented method and tune blending on validation data only.

Next add multilingual sentence embeddings and compare their content branch with
TF-IDF, then evaluate quality-dependent blending against the matching fixed hybrid.
Two-Tower and LightGCN are outside the initial scope. FAISS and MLflow are proposed
implementation options; their necessity remains open.

## Shared temporal protocol

Protocol **`deskdrop-temporal-v1`** was approved by Florian on 2026-10-19.
The authoritative rules, examples and change-control policy are in
[benchmark v1](benchmark-proposal.md).

Train before 2017-01-01T00:00:00Z, validate during January, and test during February
2017. All intervals are half-open. Evaluate one request per user at each holdout's
start with the same available, unseen candidate policy for every model.
T08 implements this through [published snapshots and one shared loader](snapshots.md);
models use its candidates/history helpers rather than selecting their own raw rows. Fit features,
models, thresholds and normalization from permitted earlier history only.

Use all five interaction types as consumption/relevance events, approved event
weights and exact-row deduplication. Minimum user history is five distinct eligible
articles, calculated on the fitting history only. After validation, freeze parameters
and retrain on pre-test history; report changed populations and cold-start slices.

Metadata must predate each interaction/request. Same-second lifecycle ambiguity,
removal-only records, pre-share events and removed items follow the approved exclusion
precedence. Report exclusion counts without double counting overlapping groups.
See [verified source findings](data-profile.md) for raw measurements.

Report Recall@10, NDCG@10, catalog coverage, language slices and response time.
Relevant positives are distinct eligible future user/article pairs; users without
eligible positives are excluded from averaged ranking metrics and reported separately.
Coverage uses the fixed request-time catalog, not a sum of user-specific candidate
lists. Changed benchmark rules require a new protocol version and affected reruns.
The proposed API latency target is under 300 ms; hardware, load and measurement
boundary remain to be specified.

## Metadata experiments

Keep interactions, temporal cutoffs, test users and candidate eligibility fixed.
Compare original metadata with controlled title removal, text shortening and altered
language labels at proposed rates of 0%, 10%, 30% and 50%. Use recorded seeds and
matched corruption samples. Regenerate affected features and quality outputs.
Define shortening severity and label substitutions before running experiments.

Compare each fixed hybrid with the corresponding quality-dependent hybrid. Do not
assume quality signals improve results. Record paired user-level metric differences.

## Manual review

Target 200 varied records reviewed independently by two reviewers. Separate rule
calibration records from held-out validation records. Preserve selection method,
labels, disagreements and adjudication. Assess agreement and per-check precision
and recall where reviewed labels support them. Broader population claims require
accounting for any oversampling of flagged records.

## Reproducibility

Each run manifest records raw checksums, transformations, snapshot/version IDs,
cutoffs, exclusions, candidate rules, parameters, seeds, quality rules, dependencies
and code revision. Generated outputs stay outside Git.
