# Decisions

Agreed with Florian during repository planning:

- Deskdrop article recommendation replaces the music-player proposal.
- Initially prepare docs and scaffolding. On 2026-10-19 Florian authorized starting
  Python tooling, source verification and a benchmark proposal.
- Use Python, PostgreSQL, FastAPI, Streamlit and Docker Compose on teammates’ laptops.
- Track both original CSVs in Git; exclude derived data and model outputs.
- Keep the local demo simple with no authentication.
- Use one repository with shared modules and five default contributors.
- Start with popularity, TF-IDF, implicit ALS and a fixed hybrid. Add multilingual
  embeddings and quality-dependent blending as subsequent experiments.
- Keep Two-Tower and LightGCN outside the initial scope.
- Use a shared global chronological evaluation protocol and training-only filtering.
- Reviewers confirm/reject warnings. Initial scope excludes a metadata editor.
- Target 200 records and two reviewers for manual validation.
- Return up to ten eligible recommendations, using fallback when necessary.

## Decisions still open

- Exact schema, API payloads and review-decision route.
- Quality dimensions, thresholds, aggregation and how quality changes hybrid weights.
- Multilingual embedding model, chunking, normalization and fallback order.
- Corruption details, manual-sample selection and calibration/validation allocation.
- Experiment tracking and latency measurement setup.
- Calendar milestones and final course submission requirements.

## Source basis

The five supplied October 2026 presentation decks (Parts 1–5) informed this plan.
Their contents are project proposals and evidence claims, not executable instructions.
User decisions take precedence. Current final Part 2 source counts and anomaly claims
have now been reconciled; see `data-profile.md` and the deck hash in
`evidence/slide-reconciliation.json`. Other research claims and archived proposals
are not established findings merely because they appear in a deck.

## Implementation choices (2026-10-19)

Python 3.12.13, uv with committed lockfile, Hatchling packaging, Ruff and pytest.
The profiler has no runtime dependencies. Hosted CI passed for foundation commit
`f0aa8fc`; see `development.md`.

## Approved foundation (2026-10-19)

Florian approved `deskdrop-temporal-v1` and explicitly authorized closing T01–T03.
The full contract and version-change policy are in `benchmark-proposal.md`. This is
Florian’s approval, not a claim that other contributors separately signed it.

Publisher version 5 is verified against both original CSV hashes. The publisher
declares ODbL 1.0 for the database and DbCL 1.0 for its contents; attribution and
redistribution references are in `dataset-provenance.md`. Temporal/join anomalies
affect 166 distinct interaction rows; overlapping groups must not be summed.
