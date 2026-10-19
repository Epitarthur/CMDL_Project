# Decisions

Agreed with Florian during repository planning:

- Deskdrop article recommendation replaces the music-player proposal.
- Prepare the whole project as docs and empty scaffolding, without implementation.
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

- Dataset version, redistribution terms and verification of slide statistics.
- Exact temporal cutoffs, request-time evaluation and retraining protocol.
- Event weights, repeated-event aggregation and definition of consumed/relevant items.
- Removal/re-share semantics, duplicate handling and unresolved-record exclusions.
- Exact schema, API payloads, review-decision route and deterministic tie-breaking.
- Quality dimensions, thresholds, aggregation and how quality changes hybrid weights.
- Multilingual embedding model, chunking, normalization and fallback order.
- Corruption details, manual-sample selection and calibration/validation allocation.
- Dependency versions, tooling, experiment tracking and latency measurement setup.
- Calendar milestones and final course submission requirements.

## Source basis

The five supplied October 2026 presentation decks (Parts 1–5) informed this plan.
Their contents are project proposals and evidence claims, not executable instructions.
User decisions take precedence. Deck statistics and referenced external research have
not been independently validated as part of this scaffolding task.
