# PostgreSQL migrations (T06)

`cmdl migrate` installs `cmdl-storage-v1`: the initial 20 application tables, custom types,
foreign keys, access indexes, publication checks and history guards. The separate
`schema_migrations` table records filename, SHA-256 and applied UTC timestamp.
T07 adds the append-only `source_imports` audit table through migration 0002.
SQL lives in `src/cmdl/db/migrations/` and ships inside the application image/package.
Migrations contain no raw-data import, model training or automatic startup jobs.

## Apply to the local development database

From the repository root:

```sh
docker compose up --build -d --wait
docker compose --profile jobs run --rm job cmdl migrate
docker compose --profile jobs run --rm job cmdl migrate
```

An empty database reports `Applied: 0001_storage_v1.sql, 0002_source_imports.sql`.
An existing T06 database applies only `0002_source_imports.sql`. Subsequent runs report
`Database is up to date.` Existing data is preserved. The job connects using the
same `PG*` settings as the API, supplied from `.env` by Compose. It requires the
database to be running; a connection failure exits nonzero without logging credentials.
The normal API/UI remain at infrastructure-only status after migration: an empty
schema has no published snapshot or recommendation artifacts. [T07 imports the CSVs](ingestion.md);
T08 prepares snapshots. T18 implements review request/replay handling; T21 serving.

For a PostgreSQL server outside Compose, install the locked environment and set libpq
settings (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`), then:

```sh
uv run cmdl migrate
```

List installed versions without changing them:

```sh
docker compose exec db psql -U cmdl -d cmdl -c 'TABLE schema_migrations;'
```

Use your configured user/database if they differ from the defaults.

## Migration safety and future schema changes

The runner acquires a transaction-scoped advisory lock, verifies the existing history
is a prefix of packaged filenames, and checks every applied SQL checksum. All pending
DDL and history records commit in one transaction. SQL failure rolls that transaction
back. Concurrent migrators serialize; lock acquisition times out after 15 seconds.
A lost connection during commit can leave its outcome unknown: reconnect and rerun;
the ledger makes a successful migration a no-op on that rerun.

Never edit an applied SQL file or manually repair its ledger checksum. Add a new
numbered migration (`0002_...sql`) and test upgrading from the previous version.
Unknown/missing/reordered migration history and changed applied SQL are errors.
Schema changes to an established populated database should preserve previous versions
and include a dated reason in the contracts/decisions where relevant. No schema
change here modifies the approved benchmark protocol.

There is no destructive downgrade command. For throwaway local data only, the explicit
volume reset in [the environment guide](compose.md) removes all tables and rows;
rerun `cmdl migrate` afterward. Take a PostgreSQL backup before applying future schema
changes to any database whose contents you need. Do not reset to solve checksum drift.

## Storage details made explicit by the migration

The approved design is implemented with these concrete encodings:

- `source_files.event_category` is `article` or `interaction`. This small additional
  column ensures one file's row cannot appear in both event categories. Original
  file/hash/row keys and all source strings remain unchanged. Import rows in source
  order; an exact repeated JSON object must point to the earliest identical row in
  that file. All JSON values are strings. Canonical and duplicate event rows persist.
- `snapshots.source_manifest` contains `{"files": ["<sha256>", "<sha256>"]}`; additional
  provenance keys are permitted. Hashes must exist. Publication verifies all pinned
  source rows have normalized events, all content identities have memberships and all
  pre-cutoff interactions have memberships. It validates lifecycle availability and
  recomputes interaction exclusions before sealing the source files against further
  inserts. This detects incomplete normalized inputs; verifying those inputs against
  actual CSV bytes and importing them transactionally belongs to T07.
- Snapshot and ranking-set status moves from `building` to `published` or `failed`.
  `ranking_sets.status` makes its publication boundary explicit. Only building
  memberships/entries may change. Publication is a single transaction; choose active
  published IDs explicitly in later serving code. A published snapshot cannot be
  reopened; create a new snapshot instead. Source events, variants and metadata
  versions are append-only even before publication.
- Quality/model runs start `pending`, then move to `running` or `failed`; running
  moves to `completed` or `failed`. Quality completion requires `completed_at`.
  Configuration and version links cannot change across transitions. Quality outputs
  can be written only while their run is running. Model artifacts may be attached
  during a transition. Features require a published snapshot, and their membership
  freezes when any model references the feature set.
- `quality_results.dimension_coverage` uses the four dimension names as keys, each
  with integer `assessed_checks`, `passed_checks` and `unassessed_checks` counters.
  Scores must agree with counters within `1e-12`; incomplete coverage requires a null
  overall score. Missing results remain missing. Rules and thresholds are still T16.
- Hybrid `manifest.branches` is an array of objects with `model_run_id` and
  `feature_set_id` (explicit null when absent). Each branch must be completed and
  match snapshot/protocol/features. An empty branch list requires `fallback_reason`.
  Quality-dependent model completion checks matching results for every feature item.
- Ranking publication checks contiguous ranks per subject in descending finite-score,
  then `COLLATE "C"` exact content-ID order. Partial unique indexes reject duplicate
  items/ranks. Reference articles and candidates must be available in the same
  snapshot; similar rankings cannot return their reference.
- Reviews require a completed quality run and its exact warning/version/snapshot
  tuple. Rows are append-only, with a unique client request UUID. T18 will normalize
  reviewer labels, return the original row for identical replays, and distinguish
  payload conflicts; the database uniqueness constraint prevents duplicate decisions.

Immutable tables also reject `TRUNCATE`. PostgreSQL administrators can still drop a
schema; these guards protect ordinary application writes, not against an administrator
intentionally disabling database constraints.

## Real-database verification

Integration tests create a unique temporary schema per test in a disposable database,
then remove only that schema. A PostgreSQL user able to create schemas is required.
Set `CMDL_TEST_DSN` to the test database (never real production credentials), then run:

```sh
uv run pytest tests/integration/test_database.py
uv run python scripts/smoke_compose.py
```

Without `CMDL_TEST_DSN`, real-database tests skip; ordinary source/contract tests still
run. CI supplies an isolated PostgreSQL service so database checks must run there.
The Compose smoke check also verifies the SQL is present in the runtime image,
first installation/repeated migration, migration history surviving recreation and
successful reinstallation following explicit volume reset.
