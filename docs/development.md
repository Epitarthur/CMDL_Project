# Development setup and verification

## Environment

Use Python 3.12.13 (`.python-version`) and uv 0.12.23 (also pinned in CI).
`uv sync --locked` installs the editable `src/cmdl` package and the locked development
dependencies. Runtime profiling uses only the standard library. Hatchling 1.27.0 is
pinned as the build backend. Future API/model packages should add dependencies when
implemented rather than installing the entire planned stack now.

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

Hosted GitHub Actions has not run in this session; no commit or push was made.
Team review remains part of T03 acceptance. T02 still needs upstream version/terms
and slide reconciliation; T01 remains a benchmark proposal awaiting team approval.
