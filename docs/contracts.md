# Shared contracts

## Identifiers and provenance

Expose article and user IDs as strings to preserve every digit. Parse timestamps
in UTC. Each output must identify its data snapshot and relevant metadata, model,
feature or check version. Preserve source values and report excluded records.

Quality scores and features must refer to the same metadata version. A warning is
an inspection signal, not a confirmed error. Missing quality results mean unavailable,
never good quality. Keep dimension scores and field reasons visible.

## Planned API responsibilities

- `GET /recommend/{person_id}`: ordered eligible articles, explanations supported by
  the model, fallback reason where applicable, model/snapshot IDs and request time.
- `GET /similar/{content_id}`: similar eligible articles for the anonymous/article view.
- Article lookup: original descriptive fields, metadata version and availability.
- `GET /quality/{record_version_id}`: version-specific checks, scores and warnings.
- `GET /quality/report`: collection summaries.
- Review submission: persist confirm/reject decisions with warning ID, record version,
  reviewer label and UTC timestamp. Exact route and payload remain to be specified.

Reader and reviewer screens require no authentication in this local demo. A reviewer
label is provenance, not a verified identity. Review decisions never edit source data.
A future correction must create a new metadata version and trigger matching checks.
No correction editor is included initially.

## Ranking behavior

Prepare 50 candidates per user initially. The service excludes unavailable and already
consumed articles, then uses an eligible fallback if needed. Return up to ten results;
never pad with ineligible articles. Define a deterministic tie-break before evaluation.
Fallback order and read-event semantics must be agreed before implementation.

The UI preserves service order and versions. Show short/empty lists honestly. On
failure show an error and retry, without displaying a stale successful result.
