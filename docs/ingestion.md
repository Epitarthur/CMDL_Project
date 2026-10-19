# Deskdrop ingestion (T07)

`cmdl ingest` validates both CSVs against their manifest, then commits source records,
identities, events, original share metadata and an exclusion audit in one transaction.
Protocol: `deskdrop-temporal-v1`. Import mapping: `deskdrop-import-v1`.

## Run and inspect

From the repository root, start the services and apply pending migrations:

```sh
docker compose up --build -d --wait
docker compose --profile jobs run --rm job cmdl migrate
docker compose --profile jobs run --rm --user "$(id -u):$(id -g)" job cmdl ingest
```

The first import prints `Imported: <UUID>`. Repeating the last command prints
`Already imported: <same UUID>` and exports the same report. It does not add rows or
rewrite earlier metadata. The command uses the same PostgreSQL environment as the API.
For a PostgreSQL connection outside Compose, configure the `PG*` variables described
in [migrations](migrations.md) and run:

```sh
uv run cmdl ingest
uv run cmdl ingest --raw-dir /path/to/raw --output-dir /path/to/audit
```

Inspect ignored local `artifacts/source-import/import.json` (complete audit) and
`import.md` (summary). PostgreSQL `source_imports` retains every completed import's
manifest/report independently of those local exports. Audit files contain source
hash/row references, not copies of the article text. A different input bundle gets
a different import UUID and audit; local export filenames show the most recent
successful export, while the database retains older audits.

Ingestion does not publish a snapshot or train a model. Product readiness remains
503 until those later dependencies exist. Snapshots and cutoff/candidate/consumption
rules are T08; minimum-history and signal aggregation are T10.

## Validation and preservation

Both files must have the exact Deskdrop headers, valid UTF-8/CSV structure, supported
event names, exact signed-digit string IDs, representable Unix timestamps and no NUL
characters. Byte sizes and SHA-256 values must match the manifest. The importer also
requires nonblank `source_url` and `upstream_version` provenance. Profiling still
accepts minimal manifests for inspection; importing requires those provenance fields.

The parser hashes and parses the same in-memory byte buffer, so a second filesystem
read cannot substitute unverified contents. Row numbers count CSV records after the
header, including quoted multiline text as a single record. Original files remain
untouched. All source field values, including empty strings, whitespace and Unicode,
are stored in `source_rows.raw_fields`; normalized IDs stay TEXT and event times become
UTC-aware timestamps. Processing timestamps never replace historical event times.

User identities are the union of interacting users and article authors. Article
identities include removal-only and interaction-only stubs. A stub is not a candidate
and does not supply metadata. Metadata versions come only from `CONTENT SHARED` rows;
removal rows' copied fields stay in raw provenance and never become metadata versions.
The `original` metadata variant records the field mapping. Payload hashes use sorted,
compact UTF-8 JSON of title/text/url/lang/content_type, without text normalization.

Every raw event persists, including exact copies. Within each file, complete field
tuples identify exact duplicates, pointing to their earliest source row. Repeated
user/article pairs with different event details remain distinct genuine events.
Event and original-metadata UUIDs are deterministically derived from namespace
`NAMESPACE_URL` and `cmdl:<kind>:<file SHA-256>:<row number>`; source IDs never pass
through floating point.

## Import identity, replays and failures

Migration `0002_source_imports.sql` adds the append-only successful-import ledger;
it does not alter applied migration `0001_storage_v1.sql`. Import identity is a UUIDv5
of `cmdl:<import version>:<protocol>:<article SHA-256>:<interaction SHA-256>`. The ledger
also enforces uniqueness of that tuple and references both source files. The ledger
retains the original file-entry manifest; T08 constructs the snapshot manifest
with its distinct array-of-hashes encoding from these referenced hashes. Its report
and all imported rows commit together. A transaction-scoped advisory lock serializes
concurrent importers, with a 15-second lock timeout. There is no automatic importer
at service startup.

A replay revalidates the supplied bytes and provenance, then returns the stored audit.
This remains a no-op after a snapshot has sealed those source files. If one input file
is shared with an earlier completed bundle, that file's rows are reused; only the new
file is imported. Snapshots must pin the intended source hashes rather than combining
all historical versions in the database. A manifest provenance change under unchanged
file hashes is a conflict, not a silent rewrite. Files already present without a
completed import ledger entry are rejected for inspection rather than auto-repaired.

Malformed inputs fail before database writes. Constraint/reconciliation failures roll
back both files, identities, metadata and the ledger. The CLI exits nonzero and writes
a timestamped diagnostic under `artifacts/source-import/failures/` when that output
location is writable. It never writes outputs inside the raw directory. Database
connection errors are sanitized; credentials are not copied into diagnostics.

The database transaction and host filesystem cannot commit atomically together. If
export fails after commit, the CLI says the import committed and exits nonzero; rerun
to export the stored audit. Each exported file is replaced atomically. A failed attempt
does not remove an older successful report, so always check the exit code and import
UUID. If the connection is lost at commit, rerun to resolve the outcome from the ledger.

## Reading the exclusion audit

`stored` reconciles source rows, article events, share metadata versions and interactions.
`identities` distinguishes interacting users, authors, their union, and unresolved /
removal-only article populations. These are scoped to the two input files, not every
historical import in the database.

`interaction_partition` is disjoint and sums to the number of raw interaction rows:

1. `source_duplicate`: exact copies after the first.
2. Among canonical rows, `unresolved`, `removal_only`, `pre_share`, `ambiguous`, then
   `removed`, using the approved benchmark precedence.
3. `canonical_eligible`: canonical rows with no temporal exclusion, before any fitting
   cutoff, minimum-history filter or request-time consumed/candidate filtering.

`interaction_temporal_counts` includes **all** raw interactions, even duplicate copies,
with one precedence-selected reason per row or `no_temporal_exclusion`. Do not add this
table to the disjoint partition. `audit_rows` lists source hash/row number for every
interaction with a duplicate pointer or temporal exclusion, retaining both attributes
when they overlap. Canonical, unflagged rows are omitted from that list but retained
in storage and totals. Article-source duplicate counts are in `sources`, and their
row pointers are retained in PostgreSQL `source_rows`.

Exclusions are audited against the imported files using the database's tested lifecycle
functions. A lifecycle event at exactly the interaction timestamp is ambiguous;
conflicting same-second events remain ambiguous until a later unambiguous event.
This full-source audit is not a published training or evaluation population. T08 must
apply the selected snapshot/cutoff and retain duplicate flags separately from exclusions.

## Verification

```sh
# CMDL_TEST_DSN must point to a disposable PostgreSQL test database.
uv run pytest tests/integration/test_ingest.py
uv run python scripts/smoke_compose.py
```

Integration tests verify repeat and concurrent imports, complete raw/metadata
preservation, malformed-input rejection, atomic rollback, provenance conflicts,
replay after publication, reuse of a sealed shared file, overlapping duplicate /
temporal exclusions, export failure/recovery and the full original Deskdrop counts.
The Docker check imports the real CSVs twice from the packaged command and checks
that import history survives recreation and disappears only after explicit volume reset.


## Verified original-source result — 2026-10-19

Import `8a46c7b5-ce33-590f-acf7-e2d4a9198313` retains 75,434 source rows:
3,122 article events and 72,312 interactions, plus 3,047 share metadata versions.
The interaction partition is 11 copies + 43 removal-only + 95 pre-share + 12 ambiguous
+ 16 removed + 0 unresolved + 72,135 canonical eligible = 72,312. These counts are
before snapshot/training filters. Both full-dataset integration and Docker replay
checks pass without increasing records. See [detailed evidence](development.md).
