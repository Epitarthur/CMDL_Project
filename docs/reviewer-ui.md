# Reviewer interface — T23 in progress

Launch with `uv run streamlit run src/cmdl/ui/app.py`, then choose Reviewer in the
sidebar. Examples mode works without a backend. Collection scenarios show partial
coverage or no assessment. Record scenarios show warnings, partial assessment,
existing decision history or unavailable quality. Inspect original metadata, select
a warning, enter a reviewer label, and confirm/reject with an optional comment.

Example saves are explicitly session-only. Refresh review data preserves these
examples during that session; restarting the session discards them. They never
write to PostgreSQL, modify original metadata, change quality scores, or count as
research annotation. Repeating the same submission reuses its request key and does
not append a duplicate example event. A changed decision gets a new key and retains
the preceding event.

Live API mode reads the snapshot-scoped collection report. Enter an exact article
ID and metadata version UUID from the reader inspection. The client loads both
article and quality and requires matching article/version/snapshot associations.
No review controls appear unless a concrete assessed warning is available. Live
saves include the warning, quality run, metadata version and snapshot, and use the
approved idempotency key. A retry after a timeout retains the same key for the same
payload. Saved histories are read from the service on refresh, with label, decision,
comment and server timestamp. Labels are unverified provenance, not authentication.

The UI uses GET /quality/report, GET /articles/{id}, GET /quality/{version}, and
POST /quality/warnings/{id}/reviews. Those product routes and durable review writes
are still T21/T18 dependencies. Live persistence/restart validation cannot be claimed
until they exist. T23 remains open for live integration and visual review.

Tests: `uv run pytest tests/unit/test_reviewer_ui.py`. These cover blank labels,
example save/replay/history, source preservation, partial and unavailable quality.
