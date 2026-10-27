# Reproducible run tracking (T09)

Use lightweight local tracking (`cmdl-run-v1`), without an MLflow service.
Runs live in ignored `artifacts/runs/<SHA-256>/`. No raw data, database schema,
benchmark rule or serving model state is changed by run capture.

Commit code/configuration and install the locked environment before capture.
The command requires a clean checkout, including untracked files, and records the
Git revision, uv.lock checksum, actual installed package versions, Python and
platform. Run this command on the host checkout; the current application container
has no Git checkout and intentionally cannot capture provenance.

Create a tracked configuration, for example `configs/popularity-run.json` when T11
exists (the following is an illustrative configuration, not an implemented model):

```json
{
  "model": "popularity-v1",
  "transformations": ["deskdrop-snapshot-v1", "training-signals-v1"],
  "parameters": {"k": 10},
  "seeds": {"model": 42},
  "numeric_tolerance": 0,
  "artifacts": {"rankings.json": "artifacts/model-output/rankings.json"}
}
```

Artifact keys are simple filenames; values are paths relative to the working
directory (or absolute local paths). Capture only after the explicit batch model job
has written its outputs. There is no training or latest-snapshot selection here.
For a deterministic algorithm record a seed even if it does not consume randomness.

With PostgreSQL PG* settings configured to the database containing T08 snapshots:

```sh
uv run cmdl run --snapshot-id d68c3efe-3eac-51ae-8bfd-5c8e43721583 \
  --config configs/popularity-run.json
uv run cmdl run --verify artifacts/runs/<printed-run-id>
```

Capture validates the published snapshot through the shared loader and retains its
immutable descriptor: source checksums/import, protocol, transformation, exclusive
UTC cutoff, metadata variant/configuration and historical metadata mapping checksum.
It copies artifacts and records their byte sizes and SHA-256 checksums. The canonical
manifest pins configuration, versions, parameters, explicit integer seeds and a
nonnegative finite numeric tolerance. No holdout labels enter it.

Identity hashes the canonical manifest, including output hashes. Input changes
therefore remain distinguishable even if they happen to yield identical rankings;
nondeterministic output changes also yield separate runs. Original source paths and
wall-clock capture times are excluded. Identical content reuses the same directory
and verifies its contents. Publication uses a temporary sibling directory and atomic
rename; interrupted staging directories are never treated as completed runs. Missing
or corrupt outputs fail verification and replay rather than being overwritten.

Exact artifact bytes are required for replay. `numeric_tolerance` documents the
model's comparison policy; tracking does not silently accept approximate rankings.
Later model tickets must regenerate inputs/rankings and establish their own numeric
reproducibility against that tolerance. T09 checks identity, byte replay and integrity
with illustrative ranking fixtures; it claims no trained-model results. Completed
local captures do not publish `model_runs` or readiness; T20 owns serving publication.

Validation: `uv run pytest tests/unit/test_runs.py`. Tests exercise changed inputs,
seeds, parameters and outputs, deterministic replay, manifest/artifact corruption,
invalid tolerances/path names and clean-checkout enforcement.
