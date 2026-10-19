# CMDL Project

Deskdrop article recommendation with metadata quality evaluation.

## Current state

Python package, locked development tooling, CI workflow and a tested source-data
profiler are implemented. Application services, models, database migrations and
Docker services remain planned. T01–T03 are complete: benchmark v1 is approved,
dataset version/license and presentation counts are verified, and CI has passed.
T04’s database schema and API contract are approved. T01–T04 are complete.

## Planned stack

Python, PostgreSQL, FastAPI, Streamlit and Docker Compose. The complete demo should
run on a teammate’s laptop. Preparation and training run as explicit batch jobs.

## Start here

- [Implementation tickets and acceptance criteria](TODO.md)

- [Architecture and repository layout](docs/architecture.md)
- [Shared API contract](docs/contracts.md)
- [Database schema specification](docs/database-schema.md)
- [OpenAPI document](docs/api/openapi.json) and [illustrative examples](docs/api/examples.json)
- [Evaluation plan](docs/evaluation.md)
- [Team ownership and delivery sequence](docs/delivery.md)
- [Decisions and open questions](docs/decisions.md)
- [Approved benchmark v1](docs/benchmark-proposal.md)
- [Dataset provenance and attribution](docs/dataset-provenance.md)
- [Source manifest](data/raw/manifest.json)

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
