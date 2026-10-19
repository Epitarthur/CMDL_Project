# Architecture

One repository, one Python application with separate modules, one PostgreSQL database.
Docker Compose runs PostgreSQL, a FastAPI bootstrap and a Streamlit status screen.
Batch preparation and training jobs run explicitly. No scheduler, Redis, pgvector or authentication initially.
Pin runtime and dependency versions when implementation starts.

## Data flow

Original CSVs -> validated snapshot -> metadata versions and event history.
Quality checks and recommendation features consume the same metadata versions.
Training produces versioned models and prepared rankings. FastAPI applies eligibility
and returns results. Streamlit displays reader and reviewer views.

## Layout

- `src/cmdl/data/`: verified profiling, transactional ingestion, published snapshots
  and the shared temporal candidate/history policy.
- `src/cmdl/quality/`: checks, dimension scores, warnings and review decisions.
- `src/cmdl/recommender/`: features, models, blending and fallbacks.
- `src/cmdl/evaluation/`: temporal splits, metrics and controlled experiments.
- `src/cmdl/api/`: service contracts and endpoints.
- `src/cmdl/ui/`: Streamlit reader and reviewer screens.
- `configs/`: future versioned data, model, quality and experiment configuration.
- `src/cmdl/db/`: migration runner and packaged, versioned SQL migrations.
- `tests/`: unit and integration checks.
- `notebooks/`: exploratory work that reuses shared modules.
- `data/raw/`: unchanged tracked source files and checksums.
- `data/processed/`, `artifacts/`: generated local outputs, excluded from Git.

## Storage

Users, article identities, article events, interactions, metadata versions, quality
runs, field warnings, review decisions, model runs and prepared rankings. Preserve
source file checksum and row number. Distinguish import idempotency from genuine
source duplicate handling. Do not infer publication dates from sharing timestamps.

The entity/key/version design is specified in [database contract v1](database-schema.md),
with [API contracts and examples](contracts.md). T04’s artifacts were approved by Florian
on 2026-10-19. T06 implements the schema with [versioned migrations](migrations.md)
and transactional publication guards. Service implementation is T21.
The T05 [development environment](compose.md) adds infrastructure and connectivity checks.
T07 adds [idempotent source ingestion and an exclusion audit](ingestion.md).
T08 adds [published temporal snapshots and the shared model input surface](snapshots.md).
Product data services remain for their implementation tickets.
