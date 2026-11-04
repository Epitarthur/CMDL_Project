# Implementation verification ? 2026-11-04

Final application: http://localhost:8501; API docs: http://localhost:8000/docs.

## Checks actually run

- Reader-feedback update: full pytest suite **204 passed** with real PostgreSQL, including fresh/repeated/concurrent migrations, language learning and preference persistence/isolation, visible impression retries, repeat downranking/expiry, stale feed/account rejection, removals and source-history preservation.
- **Nine JavaScript checks passed** via Node's test runner: four feed visibility checks (offscreen/brief visibility, focused continuous visibility, batching/deduplication and stale feeds) plus five reading tracker checks (dwell/end gating, hidden/unfocused time, rerender preservation/cleanup, navigation fencing and leaving the article). These simulate browser APIs; they are not full-browser end-to-end checks.
- Real Streamlit/API/PostgreSQL tests exercise the language selector, English-only feed/opening, failed impression submission and recovery without duplicates. A controlled real-ranker comparison verifies that one exposure is neutral and a second exposure rotates a previously top-ranked item below unseen alternatives.
- Updated real Streamlit/API/PostgreSQL test verifies no confirmation button, separate estimated-completion receipts/evidence, insufficient time and wrong-version rejection, safe retries, a simulated submission outage/recovery, and stale browser evidence after navigation. Legacy explicit receipts remain separate.
- Migrations 0004 and 0005 applied to the local demo without resetting its data. API model readiness and all three Compose health checks passed after restart; the live API exposes automatic reading estimates, reader preferences and feed visibility receipts. A fresh HTTP feed returns its delivery ID and `reader-feedback-v1` policy metadata.
- Manual browser verification of the visibility observers could not run: the browser access host is unavailable in this session. Actual browser scroll/visibility integration remains to be checked manually.
- Ruff check and format check passed for 55 Python files.
- Docker build and three healthy Compose services verified.
- Earlier Compose smoke completed with native exit code 0: migrations, import/snapshot replay, SQL connectivity, database restart/recreation persistence, and reset of its uniquely named disposable volume. The normal demo volume was preserved. The full Compose smoke was not rerun for the feedback update; fresh five-migration and live upgrade checks passed separately.
- Real HTTP reader rehearsal with a separate anonymous session: stored text, actual asynchronous worker, Up next, completion retry returning the same receipt, opening the next article without repeats, and stale-prefetch HTTP 409.
- Raw source checksums/schema/counts revalidated with cmdl profile. CSV files unchanged.
- Recorded experiment run verified with cmdl run --verify (artifact and source archive checksums).

## Final chronological comparison

Protocol: `deskdrop-temporal-v2-min5-user-item`; seed 42; validation-selected collaborative weight 0.25.

| Model | Test users | Recall@10 | NDCG@10 | Catalog coverage |
|---|---:|---:|---:|---:|
| popularity | 146 | 0.02443 | 0.01076 | 0.00861 |
| tfidf | 146 | 0.02877 | 0.01178 | 0.08787 |
| als | 146 | 0.03539 | 0.01544 | 0.14990 |
| fixed_hybrid | 146 | 0.03431 | 0.01942 | 0.13508 |
| semantic | 146 | 0.02792 | 0.01417 | 0.08959 |
| quality_hybrid | 146 | 0.03431 | 0.01942 | 0.13508 |

The same training-only five-distinct-partner user/item core supplies popularity, historical reader profiles and ALS. All request-time candidates remain available to content retrieval, including articles without interactions. All observed historical events still count for consumption exclusion.

Validation support: {"raw_pairs": 36982, "core_pairs": 33309, "raw_users": 1823, "core_users": 1047, "core_items": 1941}.
Test support: {"raw_pairs": 38806, "core_pairs": 35073, "raw_users": 1849, "core_users": 1089, "core_items": 2019}.

## Metadata experiment

All six models ran on the original 0% baseline and the nine single-field conditions (title removal, first-20-word text shortening, language-label alteration, each at 10%, 30%, 50%). Candidate sets, chronological histories and holdout populations stayed fixed; affected quality scores and content features were rebuilt.

| Field | Rate | Fixed hybrid NDCG@10 | Quality-aware NDCG@10 |
|---|---:|---:|---:|
| original | 0% | 0.01942 | 0.01942 |
| title | 10% | 0.01920 | 0.01702 |
| title | 30% | 0.02123 | 0.02038 |
| title | 50% | 0.01924 | 0.01824 |
| text | 10% | 0.01712 | 0.01712 |
| text | 30% | 0.01656 | 0.01656 |
| text | 50% | 0.01731 | 0.01818 |
| lang | 10% | 0.01942 | 0.01942 |
| lang | 30% | 0.01942 | 0.01942 |
| lang | 50% | 0.01942 | 0.01942 |

Quality weighting helped at 50% text shortening but harmed title-removal results. Language alteration is a negative control because the feature models do not consume the label; it changed warnings without changing rankings. No universal improvement is claimed.

Full language slices, warm/fallback cohorts, paired user deltas, versioned damage records, sampled IDs, source checksums and descriptors are in artifacts/evaluation/results.json. The run retains exact Python sources in source.zip and actual dependency/Python/platform metadata. Tracking run: `4a5d98c888762327e30463b4163bac25d227a3e001386a0129e24fae45d8b907`.

Preliminary developer runs used the core only for ALS. A requirements audit corrected the aggregation policy, and the tables above supersede those preliminary results. Blending weights were selected using validation only. The February set has been inspected during development; these are development holdout estimates, not a blinded confirmatory study.

## Measured API latency

After the reader-feedback update: 200 sequential full HTTP recommendations across 9 historical/cold accounts, with their existing live reading state, warm model/cache; concurrency 1. Median 23.39 ms, p95 61.28 ms, p99 144.06 ms, max 156.68 ms. Every measured request was below 300 ms. This benchmark does not submit browser impressions or create synthetic language preferences; visibility behavior is covered separately by integration/JavaScript checks.

Conditions: local Docker Desktop/WSL2, Intel Core i5-11400H 2.70 GHz, 12 logical CPUs and 16,654,311,424 bytes of memory visible to Docker; CPU-only models, API bound to localhost, one Uvicorn process, Docker-network client, SQL filtering and JSON included. This is neither a cold-start nor a concurrent-load benchmark. Samples: artifacts/latency/latency.json.

## Human review and remaining work

- Reproducible 200-record sample generated, including flagged/unflagged records: 50 calibration and 150 heldout, with independent A/B CSV templates.
- Blank templates correctly produce awaiting_labels, zero paired judgments and null agreement/kappa. Agreement and flag precision/recall await actual independent human labels; tests use explicitly synthetic labels only.
- Metadata checks are heuristics and not yet calibrated/validated by two reviewers. Corrections create new checked versions; cmdl train refreshes serving features, with outdated versions excluded until then.
- Language learning and repeated-exposure weights are serving heuristics. Their functional tests do not establish a Recall/NDCG or reader-satisfaction improvement. Historical metrics above remain the original six-model experiment without live feedback; no historical experiment was rerun or relabeled as evidence for these new policies.
- The 300 ms target passed only under the stated local single-client conditions. Production-scale load and confirmatory research on untouched data were not performed.

See README.md for exact setup, training, data placement, reviewer instructions and evaluation replay commands.
