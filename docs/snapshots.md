# Published snapshots and temporal inputs (T08)

Protocol: `deskdrop-temporal-v1`. Snapshot transform: `deskdrop-snapshot-v1`.
T08 publishes the approved request-time data and a shared model input surface.
Feature fitting, minimum-history filters, signals and models remain T09–T15.

## Prepare and inspect

After the [T07 import](ingestion.md), run from the repository root:

```sh
docker compose up --build -d --wait
docker compose --profile jobs run --rm --user "$(id -u):$(id -g)" job cmdl snapshot \
  --import-id 8a46c7b5-ce33-590f-acf7-e2d4a9198313
```

Use the actual completed import UUID if your inputs differ. This command publishes
both snapshots in one transaction and writes ignored local
`artifacts/snapshots/benchmark.json` and `benchmark.md`. Repeating it reuses the same
published IDs and membership rows, and regenerates the same report. A different
metadata variant is selected explicitly with `--variant-id`; missing historical
variant versions fail preparation rather than substituting original metadata.

Outside Compose, configure the PostgreSQL `PG*` variables and use:

```sh
uv run cmdl snapshot --import-id 8a46c7b5-ce33-590f-acf7-e2d4a9198313
```

There is no automatic latest-import/snapshot selection, scheduler or arbitrary
request-time API parameter. The Python `publish_snapshot` helper accepts an explicit
timezone-aware cutoff for chronological tests and inspection; official benchmark
preparation always uses the two approved holdout starts.

| Request / fitting data | Exclusive fitting cutoff | Holdout |
| --- | --- | --- |
| Validation / original training | 2017-01-01T00:00:00Z | January: [Jan 1, Feb 1) |
| Test / retraining after validation | 2017-02-01T00:00:00Z | February: [Feb 1, Mar 1) |

Events at a boundary belong to the following interval. An event exactly at the
request time cannot affect its consumed-item filter, fitting history or metadata.
Source lifecycle events exactly at that time make that item ambiguous for the request.

## Model input contract

All recommendation models must use `cmdl.data.snapshots.load_snapshot(connection,
snapshot_id)` with an explicit published UUID, then its shared helpers:

```python
from uuid import UUID

import psycopg

from cmdl.data.snapshots import load_snapshot

# Replace with a snapshot UUID from benchmark.json.
with psycopg.connect() as connection:
    snapshot = load_snapshot(connection, UUID("d68c3efe-3eac-51ae-8bfd-5c8e43721583"))
    catalog = snapshot.candidates()           # Fixed coverage denominator.
    unseen = snapshot.candidates(person_id="-9223121837663643404")
    history = snapshot.user_history("-9223121837663643404")
```

The returned frozen `PublishedSnapshot` contains only request-time article states,
metadata shared strictly before the cutoff, strictly earlier canonical eligible
history, and observed prior user/article consumption pairs. It does not expose the
publisher's full-source timeline or holdout audit. Unknown/unpublished snapshots,
unsupported transforms/protocols, malformed IDs and unavailable similar references
fail explicitly. Unknown well-formed users get the available catalog, with empty
history; an empty available catalog is a valid empty tuple. Source ID ordering is
exact string lexicographic order, equivalent to PostgreSQL `COLLATE "C"`, not numeric
or display-title order. This is a deterministic candidate order, not a model score.

`candidates(person_id=...)` removes every item that person interacted with strictly
before the request using any of the five event types. This includes observed events
excluded from fitting for lifecycle anomalies; using an observed read for consumption
does not make its invalid/future metadata usable for training. Duplicate copies do
not change the consumed-item set. Removal/re-share never erases prior consumption.
`candidates(reference_content_id=...)` uses the same catalog and omits its available
reference for anonymous similar-article ranking.

Fitting history independently removes exact copies and lifecycle exclusions using
benchmark precedence. Every `TrainingInteraction` carries its own earlier
`Metadata`, rather than joining a later request-time version onto an old interaction.
For example, a December interaction retains December metadata even if a January
re-share supplies the February candidate's current version. Future fitting work must
use those explicit historical versions for history features. `known_users` comes
from observed pre-cutoff interactions, not global users created by future input rows.
The model view rejects future history/metadata and normalizes timestamps to UTC even
when the database session uses another timezone.

## Availability and metadata

The source files are pinned by the completed import's separate article/interaction
hashes. Other imported revisions in PostgreSQL never enter these snapshots.
All source content identities have membership, including future-only and unresolved
stubs. Only `available` members with earlier metadata enter the catalog.

- Before any lifecycle event: `not_shared`, with no future metadata.
- Exactly at any lifecycle event: `ambiguous`.
- After an unambiguous removal: `removed`.
- Conflicting same-second share/removal events: `ambiguous` until a later unambiguous
  lifecycle event. Previously visible metadata may be retained, never used as an
  available candidate while the state is ambiguous.
- After a later unambiguous share: `available`, with that earlier share's selected
  metadata variant. Consumption history remains intact.

Metadata lookup selects the most recent strictly earlier unambiguous share group.
Exact copies use the canonical source row. Equivalent same-second shares choose the
earliest canonical source row because their descriptive payload is identical; no
subsecond order is inferred. Distinct descriptive payloads at the same second fail
closed with an explicit unknown-order error rather than choosing an arbitrary version.
A future dataset with that unresolved case requires an explicit resolution before
publication. The verified original files have one share per shared content ID.

Availability depends on lifecycle events, not metadata quality, language, text
length or variant. Metadata damage therefore cannot change candidate eligibility,
consumed-item filtering, fitting-event membership or holdout positives. Variant
coverage must include every unambiguous earlier share used by the snapshot/history.

## Identity, publication and replay

Snapshot UUIDv5 uses `NAMESPACE_URL` and `cmdl:snapshot:<descriptor SHA-256>`.
The descriptor pins import/protocol/transform versions, source hashes, normalized
UTC cutoff, variant ID/config checksum and a checksum of the concrete pre-cutoff
share-to-metadata-version/payload mapping. Config and descriptive payload checksums
are verified against stored fields. Adding future variant versions does not change
an earlier snapshot identity; changing its actual past version mapping does.

The immutable snapshot source manifest retains that descriptor and a past-history /
request-catalog report. It contains no holdout labels. Membership insertion and
publication use one transaction; official preparation publishes both snapshots
atomically. A transaction-scoped advisory lock serializes publishers (15-second lock
wait timeout). PostgreSQL's T06 guards validate source normalization, complete
membership, metadata cutoffs, availability and exclusions at publication, then freeze
the memberships and seal the pinned source files. Replays insert no source rows or
new memberships. Incomplete or incompatible existing IDs are errors, not replacements.

The local report export happens after database commit. On an export failure, rerun
to regenerate it from the same immutable inputs and published snapshots. A lost
connection at commit also requires replay to discover the outcome. Existing local
reports survive failed attempts; check the command's exit status. No migration or
approved benchmark rule changes are needed for this implementation.

## Separate holdout audit

The complete exported benchmark report additionally partitions raw interactions into
training, validation, test and after-test intervals, and audits each holdout against
its fixed request-time catalog. This is evaluation-only material; models must not
read it to fit features, filters, parameters or scores.

Holdout event categories are disjoint: exact copy, then the existing temporal/join
exclusion reason, outside the fixed catalog, already consumed before the request,
or eligible event. A share during the holdout does not expand that catalog. A removal
after the request does not change the frozen catalog, though a later interaction
while removed remains an invalid positive. Repeated eligible events count once per
distinct user/article relevance pair. All raw holdout users, users with eligible
positives, and users with no eligible positives are reported before T10 cohort filters.
These counts are not ranking metrics or evidence of model quality.

## Verification

```sh
# CMDL_TEST_DSN points to a disposable PostgreSQL database.
uv run pytest tests/unit/test_temporal.py tests/integration/test_snapshots.py
uv run python scripts/smoke_compose.py
```

Checks cover approved chronological examples, UTC half-open boundaries, re-share
consumption, unknown users/items, future metadata, earlier history versions,
conflicting timestamps, shared candidate policy, variant invariance, checksum drift,
atomic/repeated/concurrent publication, session timezones and the full original
Deskdrop source. Docker additionally verifies packaged snapshot preparation/replay,
persistence across recreation and removal only after explicit test-volume reset.


## Original-source publication — 2026-10-19

| Quantity | Validation request | Test request |
| --- | ---: | ---: |
| Available catalog | 2,788 | 2,902 |
| Raw fitting history | 66,508 | 69,403 |
| Exact duplicate copies in fitting history | 10 | 11 |
| Canonical fitting events | 66,332 | 69,226 |
| Users with eligible fitting history | 1,823 | 1,849 |
| Raw holdout events | 2,895 | 2,909 |
| Holdout events outside fixed catalog | 2,514 | 2,424 |
| Holdout events previously consumed | 128 | 140 |
| Eligible holdout events | 252 | 345 |
| Distinct relevant user/article pairs | 202 | 254 |
| Holdout users with eligible positives | 133 | 146 |
| Holdout users without eligible positives | 521 | 577 |

Validation has one duplicate holdout event; test has none. Neither holdout has
additional temporal/join exclusions in these files. The small number of remaining
positive pairs is a direct consequence of the approved fixed request-time catalog:
most heldout activity concerns articles shared during the holdout. Report denominators
and later T10 cohort exclusions explicitly; these are not full-population ranking
results. No rule was tuned or changed after observing these counts.

Published original-metadata IDs:

- Validation: `d68c3efe-3eac-51ae-8bfd-5c8e43721583`.
- Test: `1fef603f-e70e-503d-bb11-b46020ad8d5d`.

The raw interval counts are training 66,508, validation 2,895, test 2,909 and after-test 0.
Exports are `artifacts/snapshots/benchmark.json` and `benchmark.md`; original source
files and applied migrations remain unchanged.
