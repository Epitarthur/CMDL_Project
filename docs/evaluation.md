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

Use global chronological training, validation and test periods. Exact cutoffs remain
open until the event distribution is inspected. Fit features, models, thresholds and
normalization using permitted earlier data only. User/item minimum-history filters
use training data only (the Part 3 proposal is a minimum of five interactions).
Report retained and excluded populations and evaluate cold-start fallbacks separately.

All models share users, cutoffs and eligible candidate pools. At request time use only
metadata and events available by that time. Define removal/re-share semantics,
consumption events, repeat-event aggregation, relevance and validation retraining rules
before freezing the benchmark. Report unresolved IDs, duplicates and pre-share events.
Slide anomaly counts are claims to verify, not established facts in this repository.

Report Recall@10, NDCG@10, catalog coverage, language slices and response time.
Define metric denominators and treatment of users with no eligible future positives.
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
