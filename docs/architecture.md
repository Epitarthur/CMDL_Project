# Architecture

One repository, one Python application with separate modules, one PostgreSQL database.
Docker Compose will run PostgreSQL, FastAPI and Streamlit. Batch preparation and
training jobs run explicitly. No scheduler, Redis, pgvector or authentication initially.
Pin runtime and dependency versions when implementation starts.

## Data flow

Original CSVs -> validated snapshot -> metadata versions and event history.
Quality checks and recommendation features consume the same metadata versions.
Training produces versioned models and prepared rankings. FastAPI applies eligibility
and returns results. Streamlit displays reader and reviewer views.

## Layout

- `src/cmdl/data/`: ingestion, validation, snapshots and availability.
- `src/cmdl/quality/`: checks, dimension scores, warnings and review decisions.
- `src/cmdl/recommender/`: features, models, blending and fallbacks.
- `src/cmdl/evaluation/`: temporal splits, metrics and controlled experiments.
- `src/cmdl/api/`: service contracts and endpoints.
- `src/cmdl/ui/`: Streamlit reader and reviewer screens.
- `configs/`: future versioned data, model, quality and experiment configuration.
- `migrations/`: future database migrations.
- `tests/`: unit and integration checks.
- `notebooks/`: exploratory work that reuses shared modules.
- `data/raw/`: unchanged tracked source files and checksums.
- `data/processed/`, `artifacts/`: generated local outputs, excluded from Git.

## Planned storage

Users, article identities, article events, interactions, metadata versions, quality
runs, field warnings, review decisions, model runs and prepared rankings. Preserve
source file checksum and row number. Distinguish import idempotency from genuine
source duplicate handling. Do not infer publication dates from sharing timestamps.

Exact schema and Compose configuration remain implementation work.
