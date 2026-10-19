# Ownership and delivery

Default ownership, with collaboration across boundaries:

| Owner | Responsibility |
| --- | --- |
| Meiirkhan | Research questions, evidence, report and demonstration criteria |
| Florian | Data pipeline, PostgreSQL schema, API and shared version contracts |
| Arthur | Recommendation models, features and recommendation evaluation |
| Charly | Metadata checks, scores, warnings and quality validation |
| Youssef | Streamlit screens, response adapters and integration tests |

Florian and Charly agree review persistence together. Arthur and Charly agree quality
signal integration. Everyone reviews shared contracts before depending on them.

## Delivery sequence

1. Verify datasets and agree schemas, event semantics and temporal protocol.
2. Build ingestion and publish a stable versioned snapshot plus API examples.
3. Develop baseline models and quality checks; build UI against example responses.
4. Integrate one reader request and one persistent warning review end to end.
5. Validate flags and compare models, then run controlled metadata experiments.
6. Package reproducible Compose startup, preparation commands, reset instructions,
   integration evidence, report and demo.

The decks propose eight project weeks. Calendar dates remain unassigned.

## Acceptance boundaries

Verify correct response order and versions, eligibility, warning-to-record matching,
review persistence without source edits, short/empty results, missing quality reports,
service failure and successful retry. Model accuracy and warning validity require
separate evaluations. Do not treat UI integration checks as proof of either.

No runnable startup instructions exist yet. Add and verify them during implementation.
