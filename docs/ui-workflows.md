# Interface workflow verification — T24 in progress

T22 supplies the reader and T23 the reviewer. Both can be exercised through the
Streamlit app before models and product services are available. Start with:

```sh
uv run streamlit run src/cmdl/ui/app.py
uv run pytest tests/unit/test_reader_ui.py tests/unit/test_reviewer_ui.py \
  tests/integration/test_ui_workflows.py --junitxml=artifacts/ui-workflows/results.xml
```

The ignored JUnit file retains the latest local execution outcome. The approved
JSON payloads are checked against the packaged examples by the reader tests.

## Automated scenarios

| Scenario | Evidence / boundary |
| --- | --- |
| Reader order and exact IDs | HTTP adapter receives a deliberately reordered service response; cards retain it without reranking or padding. |
| Original record and quality | Inspection uses the recommendation snapshot and exact metadata version. A mismatched quality response produces an error and no score. |
| Missing quality | The unavailable report is explicit; no numeric overall score appears. |
| Failed request then retry | A 503 removes the preceding reading list; the next successful request restores it. |
| Review tuple | The controlled endpoint checks warning, quality run, snapshot and metadata version in the submitted payload. |
| Lost acknowledgment | The endpoint stores the decision then raises a read timeout. Retry sends the identical payload/key and yields one stored event. |
| Fresh UI session | A separate AppTest instance loads the decision from the controlled service's independent state. This is a UI-session restart, not a PostgreSQL restart. |
| Changed opinion | A changed confirm/reject choice uses a new key; both events remain in history. |
| Original metadata | Article fields remain identical after review. |
| Stale version | A mismatched report displays an error and does not expose Save decision. |

The API stand-in uses httpx.MockTransport, approved illustrative response payloads
and independent in-memory review state. It tests the real UI and request adapters
at the HTTP boundary. It does not implement the production API, train a model, prove
ranking eligibility, measure warning accuracy or establish database durability.
No stand-in code is shipped as a product service.

## Browser review

Desktop browser checks cover switching workspaces, reader recommendations, reviewer
collection coverage and example decision controls. This is a functional first layout;
mobile/browser-matrix accessibility review remains outstanding.

## Live acceptance still required

T24 remains open until T18/T20/T21 exist. Repeat these scenarios against the actual
Compose services, with fixed published snapshots and retained requests/responses:

1. Generate real prepared rankings. Assert no consumed/unavailable articles and exact
   service order, including exhausted/empty catalogs and popularity fallback.
2. Inspect the original article and its matching completed quality run. Also exercise
   missing quality and a deliberately stale selection.
3. Save a review, refresh, recreate API/UI/database containers without deleting the
   database volume, and verify the same review ID/timestamp survives.
4. Interrupt a save response and retry its idempotency key. Verify one database row;
   change the opinion with a new key and verify both history events.
5. Cause a dependency failure, observe cleared stale UI data, restore the service and
   verify a successful retry. Retain errors without credentials or connection strings.

## Ticket branches and intended PRs

Each ticket has its own local branch. T09 is independent; UI PRs are stacked to keep
review diffs focused:

| Ticket | Head branch | PR base |
| --- | --- | --- |
| T09 | codex/t09-run-tracking | main |
| T22 | codex/t22-reader | main |
| T23 | codex/t23-reviewer | codex/t22-reader |
| T24 | codex/t24-workflows | codex/t23-reviewer |

After each preceding UI PR merges, retarget its successor to main as appropriate.
Do not close T22–T24 solely from the fixture-backed checks. All four branches are
published, with separate PRs linked in ui-pull-requests.md. The UI PRs are drafts.
Publication used the Act4rus CLI login over HTTPS: its repository push permission
is true. FLOWleplusbeau has read permission but push permission is false. Connector
branch creation returned HTTP 403 for both accounts; sandboxed CLI authentication
checks also failed, but both local logins validated outside the sandbox. No remote
main changes or merges were made.
