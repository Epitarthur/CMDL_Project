# CMDL Project

Deskdrop article recommendation with metadata quality evaluation.

## Current state

Python package, locked development tooling, CI workflow and a tested source-data
profiler are implemented. Docker Compose now provides PostgreSQL, a FastAPI
bootstrap and Streamlit reader/reviewer screens with explicit illustrative examples. PostgreSQL migrations
implement the approved storage schema. CSV ingestion preserves source lineage and
produces a repeatable exclusion audit. Published temporal snapshots supply a shared
candidate/history policy. Models and product API/UI features remain planned. T01–T03 are complete: benchmark v1 is approved,
dataset version/license and presentation counts are verified, and CI has passed.
T04’s database schema and API contract are approved. T01–T08 are complete; published snapshots, ingestion and the environment pass local verification.

## Local services

```sh
cp .env.example .env
docker compose up --build -d --wait
docker compose --profile jobs run --rm job cmdl migrate
docker compose --profile jobs run --rm --user "$(id -u):$(id -g)" job cmdl ingest
```

Then [prepare snapshots](docs/snapshots.md) using the completed import UUID.

Open [Streamlit](http://localhost:8501). See [startup, jobs, persistence and reset](docs/compose.md).
Recommendation serving remains at 503 until ranking artifacts and product endpoints exist.

## Interface preview

Run `uv run streamlit run src/cmdl/ui/app.py`, or open the Compose UI above.
Choose Reader or Reviewer in the sidebar. Examples mode works without model/API
outputs; example reviews last only for the browser session. Live product integration
is still planned. See [reader](docs/reader-ui.md), [reviewer](docs/reviewer-ui.md), and
[workflow verification](docs/ui-workflows.md).

## Stack

Python, PostgreSQL, FastAPI, Streamlit and Docker Compose. The complete demo should
run on a teammate’s laptop. Preparation and training run as explicit batch jobs.

## Start here

- [Implementation tickets and acceptance criteria](TODO.md)

- [Architecture and repository layout](docs/architecture.md)
- [Shared API contract](docs/contracts.md)
- [Database schema specification](docs/database-schema.md) and [migration commands](docs/migrations.md)
- [OpenAPI document](docs/api/openapi.json) and [illustrative examples](docs/api/examples.json)
- [Evaluation plan](docs/evaluation.md) and [snapshot preparation/model inputs](docs/snapshots.md)
- [Team ownership and delivery sequence](docs/delivery.md)
- [Decisions and open questions](docs/decisions.md)
- [Approved benchmark v1](docs/benchmark-proposal.md)
- [Dataset provenance and attribution](docs/dataset-provenance.md)
- [Source manifest](data/raw/manifest.json) and [ingestion commands and audit](docs/ingestion.md)

Original CSVs are tracked under `data/raw/`. Derived data, models and rankings are
local outputs and are excluded from Git. Keep original source files unchanged.

This repository replaces the earlier music-player proposal.

## Development and source verification

Install [uv](https://docs.astral.sh/uv/), then run from the repository root:

```sh
uv sync --locked
uv run cmdl profile
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Python is pinned in `.python-version`; uv installs that interpreter if needed.
The profiler needs only the Python standard library. It verifies manifest hashes,
byte sizes, headers, IDs, timestamps and event types before producing deterministic
JSON and Markdown under `artifacts/data-profile/` (ignored by Git). Invalid sources
exit nonzero; no records are changed or automatically excluded. Override locations
with `--raw-dir` and `--output-dir`; output must stay outside the raw directory.
Existing reports are not refreshed after a failed validation, so check the exit code.

See [verified dataset findings](docs/data-profile.md) and the
[approved benchmark v1](docs/benchmark-proposal.md).
The notebook `notebooks/source_profile.ipynb` offers an optional interactive entry
point; Jupyter is not a core development dependency.
