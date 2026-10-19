# CMDL Project

Deskdrop article recommendation with metadata quality evaluation.

## Current state

Planning documents, original datasets and empty scaffolding only. No application,
models, database migrations or runnable Docker services have been implemented.

## Planned stack

Python, PostgreSQL, FastAPI, Streamlit and Docker Compose. The complete demo should
run on a teammate’s laptop. Preparation and training run as explicit batch jobs.

## Start here

- [Architecture and repository layout](docs/architecture.md)
- [Shared contracts](docs/contracts.md)
- [Evaluation plan](docs/evaluation.md)
- [Team ownership and delivery sequence](docs/delivery.md)
- [Decisions and open questions](docs/decisions.md)
- [Dataset provenance](data/raw/manifest.json)

Original CSVs are tracked under `data/raw/`. Derived data, models and rankings are
local outputs and are excluded from Git. Keep original source files unchanged.

This repository replaces the earlier music-player proposal.
