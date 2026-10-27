# Prepared UI pull requests

Publication is blocked by GitHub access. These are the review-ready descriptions
for the separate ticket branches; no PR URLs exist yet. See ui-workflows.md for
branch bases and outstanding live acceptance.

## T22 — Build the reader interface with approved API examples

Head: `codex/t22-reader`; base: `main`.

Replace the development status screen with a reader interface that preserves service
ranking order, explains popularity fallback, and inspects matching article metadata
and quality. Explicit illustrative scenarios support personalized, short, empty and
failed responses before the product API exists. A shared live adapter passes snapshot
context and rejects metadata/quality version mismatches.

Validation: Ruff and Streamlit interaction tests cover recommendation order, detail
inspection, short results, failures/recovery and changed versions. Packaged examples
are checked against the approved contract examples. The Docker smoke check now
exercises the reader. T22 remains open pending live T21 integration; examples are
neither trained recommendations nor measured quality.

## T23 — Add the metadata reviewer screen and review adapter

Head: `codex/t23-reviewer`; base: `codex/t22-reader`.

Add a reviewer workspace with collection coverage, read-only original metadata,
field warnings, confirm/reject forms and ordered decision history. Live requests
validate snapshot/version associations and preserve idempotency keys when retrying
the same decision. Example reviews are explicitly browser-session-only.

Validation: Ruff and Streamlit tests cover blank labels, example save/replay/history,
unchanged source fields and partial/unavailable quality. T23 remains open pending
T18/T21 durable live integration. No authentication or metadata editing is added.

## T24 — Rehearse reader/reviewer workflows at the HTTP boundary

Head: `codex/t24-workflows`; base: `codex/t23-reviewer`.

Add controlled HTTP workflow checks around the real Streamlit screens/adapters.
Verify service order, missing quality, failure/retry, stale-version rejection,
a committed save with a lost acknowledgment, safe replay, history readback in a
fresh UI session and changed opinions without overwriting earlier events. Simplify
reviewer labels and metadata display after desktop browser inspection, and document
remaining live checks.

Validation: Ruff check/format pass. Full suite on this branch: 102 passed, 80 database
checks skipped without CMDL_TEST_DSN. JUnit evidence is retained under ignored
artifacts/ui-workflows/results.xml; desktop preview screenshot is under the same
folder. T24 stays open: the controlled API is an in-memory test stand-in; real
ranking eligibility, Compose integration and PostgreSQL restart persistence still
require T18/T20/T21.
