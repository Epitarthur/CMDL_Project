# Development setup and verification

## Environment

Use Python 3.12.13 (`.python-version`) and uv 0.12.23 (also pinned in CI).
`uv sync --locked` installs the editable `src/cmdl` package and the locked development
dependencies. Runtime profiling uses only the standard library. Hatchling 1.27.0 is
pinned as the build backend. The T05 runtime now includes FastAPI, Uvicorn, Psycopg,
Streamlit and HTTPX; model packages remain for later tickets. See [Docker services](compose.md).

## Commands

```sh
uv sync --locked
uv run cmdl profile
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Run from the repository root. For another source location, pass `--raw-dir` containing
both CSVs and their manifest; `--output-dir` selects the report directory outside raw
sources. The profiler exits 1 with a readable error on invalid input. It validates
both inputs before writing reports. Existing report files survive a failed run and
must not be interpreted as a fresh success. JSON is the complete machine-readable
profile; Markdown is a readable subset. Neither includes a wall-clock generation
time or absolute path, so identical bytes reproduce identical reports.

The GitHub workflow installs the lockfile, checks lint/formatting, runs tests and
profiles the tracked sources on each push and pull request. It needs no secrets or
database. Generated outputs, local environments, caches and `.env` files are ignored.

## Local validation evidence — 2026-10-19

- `uv sync --locked --offline` passed after the initial dependency download.
- Ruff lint and format checks passed for all six Python/notebook files.
- `pytest`: 12 tests passed, covering pre-share/equal-time/removal/re-share boundaries,
  conflicting lifecycle events, overlapping anomaly accounting, exact large IDs,
  duplicate/repeat counts, changed hashes, malformed CSV/header/event/ID/timestamp,
  manifest source redirection, deterministic reports and raw-directory protection.
- Both full source files passed `uv run cmdl profile`; hashes/sizes match the manifest.
- Installed with `UV_PROJECT_ENVIRONMENT=/tmp/cmdl-clean-verification` into a second,
  empty virtual environment using the lockfile and local package cache. Its installed
  `cmdl` entry point successfully profiled the sources; JSON matched the first report
  byte for byte. This checks an independent environment, not a fresh teammate laptop.
- `git diff --exit-code -- data/raw` passed: tracked source files and manifest unchanged.
- `git check-ignore` confirmed reports, `.env`, virtual environment and Ruff cache ignored.

## Hosted verification and ticket closure

Foundation commit [`f0aa8fc`](https://github.com/Epitarthur/CMDL_Project/commit/f0aa8fcaf9f5fb37f18ba318fd528668e8020020)
was pushed to `main` using Act4rus.
[GitHub Actions run 39199043980](https://github.com/Epitarthur/CMDL_Project/actions/runs/39199043980)
passed installation from the lockfile, Ruff lint/formatting, all 12 then-existing tests
and full-source profiling on a fresh Ubuntu runner. This provides the fresh-environment
CI evidence for T03; the earlier local run used a package cache.

Florian explicitly approved benchmark v1 and closure of T01–T03 on 2026-10-19.
Separate contributor signatures and manual teammate-laptop testing are not claimed.
The completion pass adds five checks (17 total tests) for the complete raw dataset's
join/overlap reconciliation and the upstream verification command's success/failure
behavior. Source version/license evidence and slide reconciliation are recorded in
`dataset-provenance.md`, `data-profile.md` and `docs/evidence/`.

## Additional reproducible evidence commands

After obtaining the metadata and version-pinned archive as described in
`dataset-provenance.md`:

```sh
uv run python scripts/verify_upstream.py /tmp/deskdrop-metadata.json /tmp/deskdrop-v5.zip
uv run python scripts/reconcile_slides.py '/path/to/CMDL-Part-2-Final.pptx'
```

The deck stays outside the repository; its checksum and checked claims are retained
in `evidence/slide-reconciliation.json`. These commands do not modify the inputs.

## T04 contract validation

The dev lockfile includes JSON Schema and OpenAPI validators. The application had
no runtime dependencies at T04; T05 adds the service runtime. The ordinary
`uv run pytest` CI command now validates
`docs/api/openapi.json`, all 23 illustrative request/response examples, their embedded
OpenAPI copies, and example snapshot/metadata/model/feature/check relationships.

64 tests pass locally after T04 (17 source/provenance tests and 47 contract checks).
Negative examples cover numeric/rounded IDs, non-UTC and invalid dates, invalid limits,
review payloads, unavailable quality with numeric scores, reordered ranks, future
metadata, incompatible model/quality versions, and incomplete coverage with an
apparently complete aggregate. These check the design artifacts; migrations and live
API enforcement are T06/T21. Readiness, ordering and review persistence still need
runtime integration tests once those services exist.


## T05 environment validation — 2026-10-19

66 tests pass, including bootstrap readiness/error-contract checks. Ruff lint and
format checks pass. The isolated Compose smoke run verifies fresh database startup,
the actual Streamlit script reaching the API and PostgreSQL, 503 product readiness,
a standalone profiling job, persistence across DB restart and Compose recreation,
and an empty database after explicit volume deletion. The normal development stack
also starts healthy, and the documented host-user batch command writes its profile
to the host artifacts directory. Original tracked data remains unchanged.

The smoke test is now part of the GitHub workflow. Local evidence uses Docker on
Florian’s machine; it does not claim a manual run on another teammate’s laptop.
See [Compose commands and reset semantics](compose.md).
