# Reader interface — T22 in progress

Run `uv run streamlit run src/cmdl/ui/app.py` from the repository root, or use
`docker compose up --build -d --wait` and visit http://localhost:8501.

Examples mode runs without the API/database. Choose a scenario, click Find articles,
and use Inspect article and quality → Load details. The approved examples cover
personalized recommendations, popularity fallback, short lists, empty catalogs and
service failure. Example user IDs and limits belong to the fixed selected fixture;
changing the input does not train a model or personalize that fixture. All example
content and quality findings are illustrative. Source fixtures are packaged with
the UI and checked against docs/api/examples.json.

Live API mode uses API_BASE_URL (default http://localhost:8000). It uses the same
reader adapter/payloads, passes the recommendation snapshot into detail requests,
and rejects metadata/quality version mismatches. T21 product routes are still
unimplemented, so a live error is expected at this stage. Failures clear previous
recommendation results; retry with Find articles or Load details.

The screen preserves service order and exact string IDs, explains fallback, displays
short/empty results, and distinguishes unassessed quality from numeric scores.
Original metadata is read-only. Record identifiers remain available under an
inspection expander. There is no reviewer submission screen yet (T23).

Validation uses Streamlit AppTest for selection, recommendation order, inspection,
short results, failure and recovery, plus an adapter version-mismatch check. Visual
browser review and full live integration remain outstanding; T22 is not closed.
