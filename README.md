# CMDL Article Recommender

A local Deskdrop reader with real recommendations, persistent reading events, metadata quality checks, a separate reviewer workspace, and reproducible chronological experiments. See [measured verification and remaining human review](docs/verification.md).

## Start locally (PowerShell, Docker Desktop running)

Run from this repository. The original CSVs are already in `data/raw/`.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d --wait
docker compose --profile jobs run --rm job cmdl migrate
docker compose --profile jobs run --rm job cmdl ingest
docker compose --profile jobs run --rm job cmdl train
```

Open **http://localhost:8501**. API documentation: http://localhost:8000/docs.
`/health` checks SQL connectivity; `/ready` becomes 200 after a readable model release is published.
The initial training command uses real popularity, TF-IDF and CPU implicit ALS with an explicitly default 0.5 collaborative blend (or a previous validation-selected weight). It does not claim a completed research comparison.
To match the multilingual demo configuration used in the recorded comparison, run `docker compose --profile jobs run --rm job cmdl train --semantic --alpha 0.25` instead. For a new research run, select the blend through the validation/evaluation commands below rather than treating this recorded weight as universally optimal. Generated model artifacts are not committed; each checkout creates them locally.
Docker installs the locked CPU dependencies, including optional semantic support. The first build and embedding-model download require internet access. No GPU, paid API or external account is required.

The development Compose configuration mounts `src` read-only and `artifacts` for generated outputs. Restart `api ui` after source edits; rebuild after dependency changes. PostgreSQL is private to the Compose network and uses a persistent named volume. `.env` and generated outputs are ignored by Git. API/UI listen on localhost only.

### Docker Hub authentication timeout on Windows

If the build fails fetching `https://auth.docker.io/token`, Docker has not reached the application build yet. On this workstation, the running 0dcloud proxy listens at `127.0.0.1:17891`. Keep that proxy running and execute these commands in the same PowerShell session:

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:17891'
$env:HTTP_PROXY = 'http://127.0.0.1:17891'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
docker compose up --build -d --wait
```

Use your proxy's actual HTTP port on other machines. These settings apply to the current terminal and its child processes; they are needed by Docker's client-side registry authentication. With an existing local image, `docker compose up -d --wait --no-build` starts the app without rebuilding. The warning that Git commit information could not be captured is nonfatal and does not cause this network error.

## Demo walkthrough

1. Choose a historical demo account, **New demo reader**, or **Anonymous**. The feed contains up to 10 eligible articles, excerpts, language labels and explanations derived from the actual ranking signals.
   **Article language** defaults to learning from your recent reading. Choose English, Português, another available language, or All languages to override that behavior. A selected language applies to the feed and Up next and can produce fewer than 10 results.
2. Select **Read article**. The stored Deskdrop text opens inside the app immediately after the view is committed. A separate worker prepares **Up next** while you read.
3. At the bottom, inspect Up next; it appears automatically without a confirmation button. Reading time plus seeing the article end records an **estimated completion** in the background. A quick scroll alone does not count, and Up next never waits for the estimate.
4. Open the next article and repeat. Views already exclude an article from subsequent suggestions; reading estimates are retained separately. Switch accounts or return to the feed to start another navigation.
5. In **Reviewer**, inspect collection coverage, dimension scores, field warnings and stored source metadata. Confirm/reject a warning with a reviewer label. Decisions and their UTC history survive restarts.
6. A correction creates a new metadata version and fresh checks. Run `cmdl train` again to refresh content features. Until then the corrected item is excluded from ranking rather than joining new metadata to old features. Original data and previous versions are never edited.

Loading, retry, unavailable-quality, short-result and empty-catalog states are explicit. Retry IDs prevent duplicate views, estimates and reviewer decisions. Navigation IDs fence stale next-article responses and reading estimates on both the server and UI. The old clearly labelled fixture prototype remains available as `streamlit run src/cmdl/ui/prototype.py`; it is not the default reader.

Automatic reading policy `active-dwell-bottom-v1`: count time while the article page is visible and focused, stopping once its end is above the viewport. The end marker must be visible when the estimate fires. Required time is `ceil(word_count / 8)` seconds (half the reading time at 240 words/minute), bounded to 10–120 seconds. Suspended timer gaps are capped; hidden/unfocused time is not credited. This heuristic estimates engagement; it does not prove reading or understanding. Estimates use `kind=estimated_complete`, with immutable evidence in `reading_completion_estimates` (active seconds, threshold and rule version). Existing `kind=complete` receipts and `/reading/complete` remain for compatibility; the reader no longer submits them. Failed estimate submissions retry automatically with the same key while that article remains open. Closing the browser before submission can lose an estimate. Live estimates never enter historical evaluation or automatically retrain the models.

## Reader feedback and repeated recommendations

Serving policy `reader-feedback-v1` adds two live adjustments after model ranking. It does not retrain ALS or change the historical evaluation pipeline.

- **Learned language:** use the latest 20 distinct articles from the last 30 days of live reading. An article contributes its strongest event: view=1, estimated completion=2, legacy explicit completion=3; retries and multiple event types do not count as separate articles. Strength halves every seven days. For automatic mode, multiply relevance by `0.25 + 0.75 * (1 + language_strength) / (1 + strongest_language_strength)`. This gradually favors chosen languages without excluding others. No live history gives no language bias. Historical Deskdrop interactions still drive the underlying models, but do not overwhelm this recent language preference.
- **Explicit language:** a selected language filters the candidate catalog, including Up next. All languages disables the learned language multiplier. Both settings retain repeated-exposure handling. Preferences persist by account, or by session for anonymous readers.
- **Visible impressions:** a card must be at least 50% visible for one continuous second in a focused, visible page. Generating a feed or leaving a card offscreen records nothing. Browser receipts are batched and safe to retry, with at most one impression per article per delivered feed. Refreshing or returning creates a new feed visit. Old visits cannot add impressions after navigation/account changes, and a delivery expires after 24 hours. Receipt retries already committed remain safe.
- **Repeated exposure:** only eligible, unopened articles can remain in the next ranking. Sum exposures over seven days with a 24-hour half-life. One exposure has no penalty; subsequent exposure strength applies `max(0.15, 1 / (1 + 1.5 * max(0, strength - 1)))`. Multiply this by relevance and the language factor. This is a temporary downranking, not a dislike label or a consumed article. Items can return as the penalty decays. Very relevant items can still appear despite a penalty; this is not random rotation.

When feedback is active, rerank the eligible catalog so alternatives outside the cached top 50 can enter the feed. API results expose the policy version, selected mode, learned strengths and per-item base score/multipliers. Explanations mention language only when the actual selection/learned signal applies. Visibility receipts retain the served article version and model release. Preferences and impressions remain separate from source interactions and benchmark inputs.

These are heuristic personalization rules, not measured improvements to Recall/NDCG or proof of dislike. Language adaptation depends on stored language labels; mislabeled articles can affect it. Browser visibility cannot prove attention, and closing the browser before submission may lose a receipt. No impressions are fabricated from past feed requests.

## Data and provenance

Place exact `shared_articles.csv` and `users_interactions.csv` exports in `data/raw/`, alongside `manifest.json` containing source URL/version, byte sizes and SHA-256 checksums. There is no fixture fallback masquerading as research data. `cmdl profile` verifies schema, signed string IDs, UTC timestamps and checksums. A missing/mismatched export fails explicitly.

The supplied publisher version 5 contains 3,122 article lifecycle rows (3,047 shares, 75 removals), 72,312 interaction rows and 1,895 interacting users. Imports retain every source row and its file hash/1-based CSV-record number. Exact whole-row copies point to the earliest matching row; modeling deduplicates only these copies, not repeated genuine activity. The actual import audit identifies 11 duplicate excess interactions, 43 removal-only, 95 pre-share, 12 equal-time ambiguous, 16 other removed-state events, and 72,135 canonical eligible events before time splits.

`cmdl ingest` is transactional and idempotent, including version-linked quality assessment. A reimport reuses its completed import UUID. Multiple distinct imports require an explicit `--import-id` for training/evaluation. Detailed reports: `artifacts/data-profile/`, `artifacts/source-import/`.

The local serving catalog is an explicit **2017-03-01 UTC** snapshot, immediately after the supplied export, with 2,983 available articles. This initializes the demo; it is not the February test snapshot. Availability comes from source share/removal history, with strictly-before semantics and conservative equal-second handling. Separate `live_catalog_events` can remove/restore available demo items as of real request time. New app views/completions never enter source interactions, historical feature fitting or benchmark labels.

Historical consumption means any of VIEW, LIKE, BOOKMARK, FOLLOW or COMMENT CREATED before the request, including observed events excluded from model fitting. New app consumption means a view, an estimated completion, or a legacy explicit completion for the selected account (or the anonymous session). All consumed/current/removed articles are filtered again before returning a prefetched result and before opening it. Anonymous identity lasts for the Streamlit session; account history persists in PostgreSQL across sessions.

## Models and quality policy

All interaction-derived training inputs (popularity, reader content profiles, and ALS) use the same iterative five-distinct-partner user/item core, computed strictly before the cutoff. Candidate metadata remains available for content retrieval even when an article has no interactions; consumption filtering retains every observed historical interaction.

- Popularity sums per-user/item `log1p` strengths using VIEW=1, LIKE=2, BOOKMARK=3, FOLLOW=4, COMMENT CREATED=5.
- TF-IDF uses title twice plus text, unigram/bigram features, sublinear term frequency, 30,000 maximum features and cosine similarity. A reader profile uses weighted historical versions; a current article adds a 50% context branch when history exists.
- Implicit ALS minimizes weighted implicit-feedback error: observed preference 1, confidence `1 + 20*log1p(sum weights)`, missing preference 0/confidence 1; 24 factors, 10 alternating iterations, regularization 0.1, seed 42. Its training-only user/item core requires five distinct partners per user and item iteratively. Content retrieval still includes items with no interactions.
For live context, batch ALS factors stay fixed. A known reader factor is blended 70/30 with the mean context-item factor when available. Content uses the current article and up to 20 recent account events, alongside the historical profile. Anonymous similarity uses the current article; anonymous session events are used for exclusion only.

- Semantic retrieval uses `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, CPU, 128 tokens, normalized vectors and FAISS IndexFlatIP. The downloaded revision is recorded. Set `CMDL_EMBEDDING_REVISION` to replay that revision. Content-hash embedding caches reuse only identical text/model revisions.
- Branch scores are clipped at zero and divided by their positive maximum over the fixed catalog. Fixed hybrid: `alpha * ALS + (1-alpha) * content`; content is TF-IDF, or an equal TF-IDF/semantic blend when enabled. Missing collaborative support gives the content branch full weight. Popularity is an explicit fallback when no personalized/context score exists. Eligible top-50 candidates per known user are precomputed, then filtered; a full-catalog fallback handles exhausted caches and recent reads.
- Quality-aware hybrid multiplies only content contribution by version-specific reliability. Reliability is `0.3*title_present + 0.7*min(text_words/100,1)`, halved for suspected title/text mismatch or excessive repetition. Collaborative evidence is preserved. Content-only candidates retain this penalty instead of normalizing it away. Missing quality uses conservative reliability 0.25.

The checker stores completeness, validity, consistency and informativeness scores, assessed/unassessed counts, and stable field/rule/version-linked warnings with suggested next steps. It checks required fields, URL structure, ISO language codes, field formats, uncertain deterministic language detection, possible equal-title/URL duplicates, title/text overlap, short text and repetition. These are reviewable heuristics, not established errors; URLs are not fetched. A dimension is the fraction of assessed rules that pass. Overall quality is unavailable for partial assessments. High averages never remove individual warnings. Language labels and URLs are not content-model feature inputs, so altering only a language label is deliberately a negative control in the historical ranking experiment. The separate live language-preference policy does use labels; its effects are outside that historical experiment.

## Reproduce research evaluation

```powershell
docker compose --profile jobs run --rm job cmdl snapshot --import-id 8a46c7b5-ce33-590f-acf7-e2d4a9198313
docker compose --profile jobs run --rm job cmdl evaluate --semantic --experiments
docker compose --profile jobs run --rm job cmdl train --semantic
```

Use the actual import UUID printed by ingestion. Training/validation/test cutoffs are global UTC: before 2017-01-01; January [Jan 1, Feb 1); February [Feb 1, Mar 1). Tune alpha on validation only, freeze it, then refit using strictly pre-test data. All models share users, fixed eligible candidates and distinct held-out implicit positives. Users with no eligible positives are counted separately; warm and fallback cohorts are both reported. Future article versions, future interactions and all live app data are excluded. Metadata associated with a training event is strictly earlier than that event.

The original repository's `deskdrop-temporal-v1` only required a five-article user minimum. This implementation records `deskdrop-temporal-v2-min5-user-item` for the requested user AND item threshold; published v1 source snapshots remain unchanged. Results from different protocols must not be treated as direct model improvements.

Reports include Recall@10, NDCG@10, catalog coverage, English/Portuguese slices and paired user outputs. Controlled damage independently removes titles, truncates text to the first 20 words, or swaps language labels (en→pt; other→en), on seeded nested 10%, 30%, 50% article samples, plus the shared original 0% baseline. Features and quality are regenerated while history, cutoffs, users and catalog membership stay fixed. ALS/popularity remain unchanged. Research damage is versioned in local artifacts and never promoted into the demo.

Outputs: `artifacts/evaluation/results.md`, `results.json`, `tuning.json`, corruption manifests, and content-addressed run manifests using the existing tracker. Model manifests retain source descriptors, code fingerprint, actual dependency versions, seed, parameters, model revision and artifact checksums. Research run manifests also retain a reproducible source.zip archive, lock checksum and the actual Python/platform/dependency environment. Development captures explicitly identify source hashes rather than pretending to be committed clean checkouts. Demo releases are marked `demo_only`.

## Human review and latency

```powershell
docker compose --profile jobs run --rm job cmdl review-sample
docker compose --profile jobs run --rm job cmdl review-metrics --labels artifacts/human-review/labels.csv
docker compose --profile jobs run --rm job cmdl benchmark --requests 200
```

The seeded sample selects 200 varied records across language, warning and length strata, including unflagged controls: 50 calibration and 150 held-out records. Two separate reviewer CSVs contain blank `actual_flag` labels; reviewers fill 0/1 independently, then concatenate rows into `labels.csv` with one header. Unknowns stay blank. Metrics validate exact record versions/subsets, exclude calibration, report agreement/Cohen's kappa, disagreements and per-rule precision/recall on agreed labels. Human outcomes are pending until real labels are supplied. The stratified sample does not support unweighted population-accuracy claims.

Latency measures full HTTP recommendations, including SQL eligibility filtering and JSON, with a warm model, one sequential client, historical accounts plus cold start. It reports median/p95/p99/max and whether the 300 ms target was met under those conditions. This is not a concurrent production load test. Reports live under `artifacts/latency/`.

## API and checks

Preserved routes: `GET /recommend/{person_id}`, `/similar/{content_id}`, `/articles/{content_id}`, `/quality/report`, `/quality/{record_version_id}`, `POST /quality/warnings/{warning_id}/reviews`. Live extensions: `/demo/accounts`, `/feed`, `POST /reading/open`, `/reading/estimate`, `/reading/complete` (legacy explicit completion), `/reading/reset`, `POST /next`, `GET /next/{request_id}`, `/quality/queue`, `POST /reviewer/articles/{content_id}/versions`. The running `/openapi.json` is the v2 implementation; `docs/api/openapi.json` and its illustrative examples preserve the original v1 design, not a claim of v2 conformance. Live serving accepts only its active release's snapshot, rather than silently combining snapshot/model versions. Reviewer labels are provenance, not authentication; this is a localhost demonstration.

Host development requires Python 3.12 and uv:

```powershell
uv sync --locked --extra semantic
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run cmdl profile
```

Set `CMDL_TEST_DSN` to a disposable PostgreSQL database for integration tests; tests create/drop only their uniquely named schemas. Focused tests cover the actual Streamlit reader against PostgreSQL/API, automatic reading estimates, legacy explicit completion, progress submission outages/retries, no repeats, removals, account switches, stale responses, cold start, empty states, quality/review version matching and temporal leakage. The browser tracker logic can also be checked with `node --test tests/browser/test_reader_progress.mjs` (Node.js required): active-time and end-visibility gating, hidden/unfocused tabs, rerenders, cleanup and stale navigation. These JavaScript checks use a simulated browser environment, not a full browser. Preserved prototype tests exercise illustrative UI adapters separately.

When updating an existing demo, run `docker compose --profile jobs run --rm job cmdl migrate`, then `docker compose restart api ui`. Migrations 0004 (reading estimates) and 0005 (reader feedback) preserve existing data; no model retraining is needed. Reader feedback operations are `GET/PUT /reader/preferences` and `POST /feed/impressions`; delivered feeds include `feed_id`. JavaScript visibility checks: `node --test tests/browser/test_reader_progress.mjs tests/browser/test_feed_visibility.mjs`.

Stop with `docker compose down`; restart with `docker compose up -d --wait`. The database and generated artifacts remain. Avoid `down --volumes` unless you explicitly want to delete the database.
