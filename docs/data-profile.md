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
| Removal-only article ID | 43 (0.059%) | 8 | High: metadata has no recorded share. Excluded from training/relevance by approved benchmark v1. |
| Before first recorded share | 95 (0.131%) | 93 | High: using later article text would leak future information. Excluded by benchmark v1. |
| Same timestamp as article event | 12 (0.017%) | 12 | Medium: within-second order is unknown. Benchmark v1 omits the article/interaction at that instant. |
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

## Time distribution and approved protocol

January 2017 contains 2,895 interactions; February contains 2,909. These give natural
calendar validation and test periods after training on earlier data. The peak month
is June 2016 with 16,315 interactions. These are source event counts, not retained
benchmark populations or evidence of traffic trends beyond this historical export.
See `benchmark-proposal.md` for approved `deskdrop-temporal-v1` rules and leakage examples.

## Reconciliation and exclusions

The raw flags partition into 95 pre-share rows, 12 equal-time rows, 16 post-removal
rows only, 40 removal-only rows only, and 3 rows flagged both removal-only and
post-removal. Thus **166 distinct interaction rows** have temporal/join flags:
43 + 95 + 12 + 19 - 3 = 166. The remaining 72,146 raw rows have none of those flags.
These are raw diagnostics before exact-row deduplication and minimum-history filters,
not a final training population. T07/T08 will publish those stage-by-stage exclusions.

Other reproduced measurements from the presentation workspace's `dataset-profile.md`:

| Measure | Verified result |
| --- | ---: |
| Naive inner join to all article events | 72,680 rows |
| Inner join to unique shared metadata | 72,269 rows |
| Distinct user/article pairs | 40,710 |
| Latest unambiguous shared-state article IDs | 2,983 |
| Distinct URLs in shared metadata | 3,023 |
| Distinct titles in shared metadata | 3,003 |

Shared-metadata language labels: en=2,211; pt=829; es=2; la=3; ja=2. These are source
labels, not validated language detections. Repeated titles/URLs are duplicate candidates,
not proof that the articles are duplicates. Latest availability is not a historic
request's catalog size. The profiler now exposes all these quantities explicitly.

## Slide reconciliation

The current canonical deck is `final/CMDL-Part-2-Final.pptx` in the supplied CMDL
Presentation workspace. Its SHA-256 and seven reproduced claims are retained in
[evidence/slide-reconciliation.json](evidence/slide-reconciliation.json):

- Slide 2: 3,047 shared articles, 72,312 interactions and 1,895 users — match.
- Slide 15: 11 duplicate excess rows, 43 interactions across 8 removal-only IDs,
  95 pre-share events, and populated core fields on every shared row — match.
- Slide 2's March 2016–February 2017 time coverage matches the verified UTC ranges.
- The available Deskdrop draft `slides/part-2-dataset.pptx` also reports 3,122 article
  events (3,047 shares + 75 removals) and the same anomaly counts — match.

The canonical deck's approximate “at or before” availability wording is superseded
by benchmark v1's precise strictly-before/same-second policy. Earlier Open Library
proposal decks are superseded and are not evidence about this Deskdrop dataset.
The check covers current source-count claims; it does not validate external research
claims in archived Parts 1–5 or claim that later project deliverables exist.

## Evidence and completion

- Sources: `data/raw/shared_articles.csv`, `data/raw/users_interactions.csv` and manifest.
- Reproduce: `uv sync --locked`, then `uv run cmdl profile`.
- Implementation: `src/cmdl/data/profile.py`; unit and full-source tests under `tests/`.
- Notebook entry point: `notebooks/source_profile.ipynb` (uses the same implementation).
- [Provenance/attribution](dataset-provenance.md): publisher version **5**; both
  decompressed CSV hashes and sizes match the upstream version-pinned download.
- [Publisher evidence](evidence/deskdrop-provenance.json): source API fields,
  download receipt and declared ODbL/DbCL licenses, checked 2026-10-19.
- [Slide verification command](../scripts/reconcile_slides.py) repeats the seven
  exact numeric/completeness claims against the canonical deck.

T02 is complete for the current sources and canonical presentation. CSV bytes are
unchanged; no original rows have been removed. Actual training/snapshot exclusions
are subsequent T07/T08 work under the approved benchmark rules.
