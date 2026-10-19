# Local Docker environment (T05)

Install a running Docker engine with Docker Compose v2 or later. No host Python or
PostgreSQL installation is needed for the three services. Run from the repository root:

```sh
cp .env.example .env
docker compose up --build -d --wait
docker compose ps
```

Open [Streamlit](http://localhost:8501) for the development status screen.
[API health](http://localhost:8000/health) verifies a real SQL query; the screen calls
this API through the Compose network. PostgreSQL has no host port. API and UI ports
bind to localhost; change `API_PORT`/`UI_PORT` in `.env` if occupied. These credentials
are only for local development; `.env` is ignored by Git. Do not reuse real credentials.

The image runs as an unprivileged user. Dependencies are installed from `uv.lock`.
Base images use Python 3.12.13 and uv 0.12.23. PostgreSQL 17.11 is pinned by
image digest (multi-platform manifest). Update that digest deliberately when taking
PostgreSQL patches, then rerun the smoke check.
The application image is shared by API, UI and explicit jobs. Source edits require
`docker compose up --build -d --wait`; there is no automatic host-code mount.

## Health versus application readiness

Compose checks PostgreSQL using `pg_isready`, API using `/health` (SQL `SELECT 1`),
and Streamlit using `/_stcore/health`. API waits for healthy PostgreSQL; UI waits for
healthy API. The development screen additionally verifies the UI → API → SQL path.

`GET /ready` returns the approved `DependencyUnavailable` error with HTTP 503 even
when infrastructure is healthy: no published snapshot or ranking artifacts exist yet.
It also returns 503 on database failure. This is expected at T05. Migrations are T06,
product endpoints/readiness verification T21 and reader/reviewer screens T22/T23.
`/health` is an internal development endpoint outside the product API contract.
The generated FastAPI docs describe only this bootstrap; the complete target contract
remains `docs/api/openapi.json`. No model data is fabricated during service startup.

## Explicit batch jobs

```sh
docker compose --profile jobs run --rm --user "$(id -u):$(id -g)" job cmdl profile
```

This verifies the original CSVs and writes JSON/Markdown to host `artifacts/data-profile/`.
Raw inputs are mounted read-only. The job exits after completion and does not depend
on running services. Outputs are ignored by Git. The explicit user option writes outputs as your host user on Linux and macOS.
The service image otherwise defaults to unprivileged UID 10001. Ingestion/training commands will be added with their
tickets; they are not scheduled or run automatically at startup.

## Stop, persistence and reset

```sh
docker compose down
docker compose up -d --wait
```

Ordinary shutdown/recreation preserves the named `postgres_data` volume. Changing
credentials in `.env` does not change users already initialized inside an existing
volume; retain the original credentials or explicitly reset the local database.

To **delete this project's database contents** and initialize an empty database:

```sh
docker compose down --volumes
docker compose up -d --wait
```

This does not remove raw CSVs or host batch artifacts. To regenerate a profile, rerun
the explicit profile job. Keep a stable Compose project name (`cmdl` by default):
changing it creates a different volume and does not migrate old data. Do not run a
volume reset on a database whose contents you need.

## Verification and troubleshooting

```sh
docker compose logs --tail=100 db api ui
curl --fail http://localhost:8000/health
curl -i http://localhost:8000/ready
uv run python scripts/smoke_compose.py
```

The smoke command builds and starts a uniquely named temporary project on temporary
localhost ports. It runs the actual Streamlit script against the API, checks honest
503 readiness, writes a probe row, verifies it survives DB restart and Compose
recreation, then verifies `down --volumes` produces an empty database. It always
cleans up only its own project and volume. It requires the dev environment installed
with `uv sync --locked`. It does not touch the normal `cmdl` development volume.
Docker builds require network access on the first run. If interrupted by a process
kill, use `docker compose ls` to identify the `cmdl-smoke-*` project before cleaning
that specific project; never use global volume pruning.
