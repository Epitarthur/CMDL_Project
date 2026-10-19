# Verified source dataset

Measured on 2026-10-19 with `uv run cmdl profile`. Source identity is the exact
SHA-256 pair in `data/raw/manifest.json`; the profiler checks both hashes and sizes
before interpreting rows. Outputs are deterministic `artifacts/data-profile/profile.json`
and `profile.md`, ignored by Git. The implementation and synthetic contract tests
are tracked. No source records are changed or excluded.

## Grain and observed counts

| Measure | Observed |
| --- | ---: |
| Article event rows | 3,122 |
| Distinct article-event content IDs | 3,057 |
| Share events / distinct shared articles | 3,047 |
| Removal events | 75 |
| Removal-only content IDs | 10 |
| Content IDs with both share and removal | 64 |
| Interaction rows | 72,312 |
| Interaction users | 1,895 |
| Interaction content IDs | 2,987 |
| Exact duplicate interaction excess rows | 11 |
| Repeated user/article pairs | 16,640 |
| Interactions beyond the first per user/article pair | 31,602 |

Article events span 2016-03-28T19:19:39Z through 2017-02-28T18:51:11Z;
interactions span 2016-03-14T13:54:36Z through 2017-02-28T19:21:51Z.
There are no exact duplicate article rows, multiple shares of the same content ID,
or conflicting share/removal events at the same timestamp in these files.

## Findings and downstream risks

Counts below use all 72,312 raw interaction rows as the denominator. Groups overlap;
the JSON report's `interaction_anomaly_combinations` partitions the entire population
and must be used when reconciling a union of exclusions.

| Finding | Rows | Content IDs | Risk and response |
| --- | ---: | ---: | --- |
| No matching article-event ID | 0 | 0 | Join coverage holds at source-ID level; it does not prove availability. |
| Removal-only article ID | 43 (0.059%) | 8 | High: metadata has no recorded share. Propose excluding from training and relevance pending team review. |
| Before first recorded share | 95 (0.131%) | 93 | High: using later article text would leak future information. Propose exclusion. |
| Same timestamp as article event | 12 (0.017%) | 12 | Medium: within-second order is unknown. Propose conservative exclusion at that instant. |
| After removal, before any later share | 19 (0.026%) | 8 | High: source activity does not establish catalog eligibility. Apply the same temporal policy in training and evaluation. |

Confidence in these counts is high for the supplied bytes. Severity describes the
potential methodological impact, not the number of affected rows. Causes are unknown:
source history may be incomplete, but the CSVs cannot prove that explanation.

All titles, text, URLs and language fields are populated, including on removal rows.
This does not establish their validity or informativeness. Article author-agent/region/
country fields are each 78.22% missing; interaction agent/country are 21.29% missing,
and region is 21.30% missing. Treat optional context as sparse rather than requiring
it for recommendation. Exact duplicate excess is only 0.015% of interactions; repeat
user/article activity is much larger and must not be conflated with exact duplicates.
A naive join against article events can multiply interaction rows for repeated content IDs.

## Time distribution and proposed next step

January 2017 contains 2,895 interactions; February contains 2,909. These give natural
calendar validation and test periods after training on earlier data. The peak month
is June 2016 with 16,315 interactions. These are source event counts, not retained
benchmark populations or evidence of traffic trends beyond this historical export.
See `benchmark-proposal.md` for reviewable rules and leakage examples.

## Evidence and remaining verification

- Sources: `data/raw/shared_articles.csv`, `data/raw/users_interactions.csv` and manifest.
- Reproduce: `uv sync --locked`, then `uv run cmdl profile`.
- Implementation: `src/cmdl/data/profile.py`; edge cases: `tests/unit/test_profile.py`.
- Notebook entry point: `notebooks/source_profile.ipynb` (uses the same implementation).
- The [upstream Kaggle page](https://www.kaggle.com/datasets/gspmoreira/articles-sharing-reading-from-cit-deskdrop) was checked on 2026-10-19 but exposed no readable license
  or version information through the available web reader. Redistribution terms and
  upstream version remain unverified. The manifest establishes local identity only.
- Presentation statistics have not been reconciled against the five slide decks yet.
  T02 therefore remains in progress; no claims of full provenance verification.
