"""Real PostgreSQL, real ranker, actual API routes; no mock recommendation payloads."""

import csv
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from cmdl import serving as s
from cmdl.api.app import app
from cmdl.data.ingest import ingest
from cmdl.data.profile import ARTICLE_COLUMNS, INTERACTION_COLUMNS
from cmdl.data.snapshots import load_snapshot, publish_snapshot
from cmdl.db.migrate import migrate
from cmdl.reading import ESTIMATE_RULE
from cmdl.recommender.models import Ranker
from cmdl.recommender.training import publish


@pytest.fixture
def reader_stack(empty_db, tmp_path, monkeypatch):
    migrate(empty_db)
    raw = tmp_path / "raw"
    raw.mkdir()
    files = []
    for name, columns in (
        ("shared_articles.csv", ARTICLE_COLUMNS),
        ("users_interactions.csv", INTERACTION_COLUMNS),
    ):
        with (raw / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            if name.startswith("shared"):
                events = [(i, "1", 100, "CONTENT SHARED") for i in range(1, 10)]
                events += [(10, "1", 9999999999, "CONTENT SHARED")]
            else:
                events = [
                    (i, str(u), 200 + i + u, "VIEW") for u in range(1, 7) for i in range(1, 7)
                ]
                events.append((9, "99", 300, "VIEW"))
            for item, person, at, kind in events:
                row = dict.fromkeys(columns, "")
                row.update({k: "1" for k in columns if k.endswith("Id")})
                row.update(contentId=str(item), timestamp=str(at), eventType=kind)
                if name.startswith("shared"):
                    row.update(
                        title=f"Data systems {item}",
                        text="data systems research article",
                        url="https://example.com/" + str(item),
                        lang="en" if item % 2 else "pt",
                        contentType="HTML",
                    )
                else:
                    row.update(personId=person)
                writer.writerow(row)
        payload = (raw / name).read_bytes()
        files.append(
            {"file": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        )
    (raw / "manifest.json").write_text(
        json.dumps({"source_url": "https://example.com", "upstream_version": 5, "files": files})
    )
    imported, _ = ingest(empty_db, raw)
    published, _ = publish_snapshot(
        empty_db, UUID(imported["import_id"]), datetime(2017, 3, 1, tzinfo=UTC)
    )
    snapshot = load_snapshot(empty_db, UUID(published["snapshot_id"]))
    ranker = Ranker(snapshot)
    assert ranker.support_counts["raw_pairs"] == 37
    assert ranker.support_counts["core_pairs"] == 36
    assert ranker.popularity[ranker.position["9"]] == 0
    assert "9" in ranker.ids  # Interaction filtering never removes content-only candidates.
    assert "10" not in ranker.ids  # Future article metadata never reaches features.
    manifest = empty_db.execute(
        "SELECT source_manifest FROM snapshots WHERE snapshot_id=%s", (snapshot.snapshot_id,)
    ).fetchone()[0]
    publish(empty_db, ranker, tmp_path / "models", manifest)
    empty_db.commit()
    # Connection.info.dsn intentionally omits the password. Preserve the supplied
    # test credentials rather than relying on Compose's PGPASSWORD environment.
    dsn = make_conninfo(os.environ["CMDL_TEST_DSN"], options=empty_db.info.options)
    monkeypatch.setattr(s, "connect", lambda: psycopg.connect(dsn, row_factory=dict_row))
    monkeypatch.setattr(
        s.POOL, "submit", lambda *args: None
    )  # Deliberately control asynchronous completion order.
    s.LOADED.clear()
    client = TestClient(app)
    return client, ranker, published


def test_reader_prefetch_retry_removal_review_and_leakage(empty_db, reader_stack, monkeypatch):
    client, ranker, published = reader_stack
    feed = client.get("/recommend/1").json()
    assert feed["returned_count"] == 3
    assert not {"1", "2", "3", "4", "5", "6"} & {i["content_id"] for i in feed["items"]}
    session = str(uuid4())

    def opening(item, person="1"):
        return {
            "session_id": session,
            "person_id": person,
            "content_id": item,
            "navigation_id": str(uuid4()),
            "client_request_id": str(uuid4()),
            "prefetch_id": str(uuid4()),
        }

    first = opening(feed["items"][0]["content_id"])
    opened = client.post("/reading/open", json=first)
    assert opened.status_code == 200
    article = opened.json()["article"]
    assert client.post("/reading/open", json=first).status_code == 200
    assert empty_db.execute("SELECT count(*) FROM live_reading_events").fetchone()[0] == 1
    assert (
        empty_db.execute(
            "SELECT count(*) FROM live_reading_events WHERE kind='complete'"
        ).fetchone()[0]
        == 0
    )
    assert (
        client.get(
            "/next/" + first["prefetch_id"],
            params={"session_id": session, "navigation_id": first["navigation_id"]},
        ).json()["status"]
        == "pending"
    )
    params = {"session_id": session, "navigation_id": first["navigation_id"]}
    with monkeypatch.context() as outage:

        def failing_recommendation(*args, **kwargs):
            raise s.Fault("Temporary ranking outage", 503, "DependencyUnavailable")

        outage.setattr(s, "recommend", failing_recommendation)
        s.prepare_next(first["prefetch_id"])
        assert (
            client.get("/next/" + first["prefetch_id"], params=params).json()["status"] == "failed"
        )
    retry = {
        k: first[k]
        for k in ("session_id", "person_id", "content_id", "navigation_id", "prefetch_id")
    }
    assert client.post("/next", json=retry).status_code == 200
    s.prepare_next(first["prefetch_id"])
    next_one = client.get("/next/" + first["prefetch_id"], params=params).json()
    assert next_one["status"] == "ready"
    next_id = next_one["result"]["items"][0]["content_id"]
    assert next_id != first["content_id"]
    completion = {k: first[k] for k in ("session_id", "person_id", "content_id", "navigation_id")}
    completion.update(
        client_request_id=str(uuid4()), metadata_version_id=article["metadata_version_id"]
    )
    assert client.post("/reading/complete", json=completion).json()["confirmed_read"]
    assert client.post("/reading/complete", json=completion).status_code == 200
    assert (
        empty_db.execute(
            "SELECT count(*) FROM live_reading_events WHERE kind='complete'"
        ).fetchone()[0]
        == 1
    )
    estimate = {
        **completion,
        "client_request_id": str(uuid4()),
        "active_seconds": 10,
        "end_visible": True,
        "rule_version": ESTIMATE_RULE,
    }
    # A bottom-only signal, insufficient dwell, invalid version and invented time all fail.
    assert client.post("/reading/estimate", json=estimate).status_code == 422
    with monkeypatch.context() as clock:
        later = (datetime.now(UTC) + timedelta(seconds=12)).isoformat()
        clock.setattr(s, "now", lambda: later)
        assert (
            client.post("/reading/estimate", json={**estimate, "end_visible": False}).status_code
            == 422
        )
        assert (
            client.post("/reading/estimate", json={**estimate, "active_seconds": 9}).status_code
            == 422
        )
        assert (
            client.post(
                "/reading/estimate", json={**estimate, "metadata_version_id": str(uuid4())}
            ).status_code
            == 409
        )
        receipt = client.post("/reading/estimate", json=estimate)
        assert receipt.status_code == 200
        assert receipt.json()["estimated_read"] and not receipt.json()["confirmed_read"]
        assert client.post("/reading/estimate", json=estimate).json() == receipt.json()
        assert (
            client.post("/reading/estimate", json={**estimate, "active_seconds": 11}).status_code
            == 409
        )
        assert (
            client.post(
                "/reading/estimate", json={**estimate, "client_request_id": str(uuid4())}
            ).status_code
            == 409
        )
    assert empty_db.execute("SELECT count(*) FROM reading_completion_estimates").fetchone()[0] == 1
    assert (
        empty_db.execute(
            "SELECT count(*) FROM live_reading_events WHERE kind='complete'"
        ).fetchone()[0]
        == 1
    )
    second = opening(next_id)
    assert client.post("/reading/open", json=second).status_code == 200
    assert (
        client.post(
            "/reading/estimate", json={**estimate, "client_request_id": str(uuid4())}
        ).status_code
        == 409
    )
    # A committed estimate remains safe to retry after navigation changes.
    assert client.post("/reading/estimate", json=estimate).status_code == 200
    assert client.get("/next/" + first["prefetch_id"], params=params).status_code == 409
    s.prepare_next(first["prefetch_id"])  # Finishing an old task cannot change navigation.
    s.prepare_next(second["prefetch_id"])
    response = client.get(
        "/next/" + second["prefetch_id"],
        params={"session_id": session, "navigation_id": second["navigation_id"]},
    ).json()
    remaining = response["result"]["items"][0]["content_id"]
    other_session = {**opening(remaining), "session_id": str(uuid4())}
    assert client.post("/reading/open", json=other_session).status_code == 200
    assert (
        client.get(
            "/next/" + second["prefetch_id"],
            params={"session_id": session, "navigation_id": second["navigation_id"]},
        ).json()["result"]["items"]
        == []
    )
    empty_db.execute(
        "INSERT INTO live_catalog_events VALUES (%s,%s,'removed',clock_timestamp(),'test removal')",
        (uuid4(), remaining),
    )
    empty_db.commit()
    response = client.get(
        "/next/" + second["prefetch_id"],
        params={"session_id": session, "navigation_id": second["navigation_id"]},
    ).json()
    assert response["result"]["items"] == []  # Prefetched result rechecked after removal.
    assert client.post("/reading/open", json=opening(remaining)).status_code == 409
    cold = client.get("/recommend/0").json()
    assert cold["items"] and all(i["source"] == "popularity" for i in cold["items"])
    assert client.get("/recommend/1?limit=11").status_code == 422
    quality = client.get("/quality/" + article["metadata_version_id"]).json()["quality"]
    warning = quality["warnings"][0]
    before = empty_db.execute(
        "SELECT title,text FROM metadata_versions WHERE metadata_version_id=%s",
        (article["metadata_version_id"],),
    ).fetchone()
    decision = {
        "client_request_id": str(uuid4()),
        "snapshot_id": published["snapshot_id"],
        "quality_run_id": quality["quality_run_id"],
        "metadata_version_id": article["metadata_version_id"],
        "reviewer_label": "reviewer-a",
        "decision": "confirm",
        "comment": None,
    }
    path = "/quality/warnings/" + warning["warning_id"] + "/reviews"
    assert client.post(path, json=decision).status_code == 201
    assert client.post(path, json=decision).status_code == 200
    assert client.post(path, json={**decision, "decision": "reject"}).status_code == 409
    assert (
        client.post(
            path,
            json={
                **decision,
                "client_request_id": str(uuid4()),
                "metadata_version_id": str(uuid4()),
            },
        ).status_code
        == 409
    )
    assert (
        empty_db.execute(
            "SELECT title,text FROM metadata_versions WHERE metadata_version_id=%s",
            (article["metadata_version_id"],),
        ).fetchone()
        == before
    )

    assert client.get("/quality/" + article["metadata_version_id"]).json()["quality"]["warnings"][
        0
    ]["reviews"]
    # A switched account fences even a ready response; it never counts as completion.
    assert (
        client.post(
            "/reading/reset",
            json={"session_id": session, "person_id": "0", "navigation_id": str(uuid4())},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/next/" + second["prefetch_id"],
            params={"session_id": session, "navigation_id": second["navigation_id"]},
        ).status_code
        == 409
    )
    # Run the actual Streamlit reader against these API routes and persistent data.
    from streamlit.testing.v1 import AppTest

    from cmdl.ui import live_client

    def local_request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise live_client.APIError(response.json()["message"])
        return response.json()

    monkeypatch.setattr(live_client, "request", local_request)
    ui = AppTest.from_file(Path("src/cmdl/ui/app.py").resolve(), default_timeout=30).run()
    ui.sidebar.selectbox[0].set_value("New demo reader").run()
    assert not ui.exception
    next(b for b in ui.button if b.label == "Read article").click().run()
    assert not ui.exception
    nav = dict(ui.session_state["navigation"])
    s.prepare_next(nav["prefetch_id"])
    ui.run()
    assert not ui.exception
    assert any(h.value == "Up next" for h in ui.subheader)
    assert not any(b.label == "Mark as read" for b in ui.button)
    from cmdl.ui import reading_progress

    def browser_evidence(navigation, text):
        return {k: navigation[k] for k in ("navigation_id", "person_id", "content_id")} | {
            "active_seconds": 10,
            "end_visible": True,
            "rule_version": ESTIMATE_RULE,
        }

    with monkeypatch.context() as browser:
        browser.setattr(reading_progress, "render_progress", browser_evidence)
        later = (datetime.now(UTC) + timedelta(seconds=12)).isoformat()
        browser.setattr(s, "now", lambda: later)
        with monkeypatch.context() as outage:

            def estimate_outage(method, path, **kwargs):
                if path == "/reading/estimate":
                    raise live_client.APIError("Temporary reading service outage")
                return local_request(method, path, **kwargs)

            outage.setattr(live_client, "request", estimate_outage)
            ui.run()
            assert not ui.exception and not ui.session_state["estimated"]
            assert any(h.value == "Up next" for h in ui.subheader)
        ui.session_state["estimate_retry_at"] = 0
        ui.run()
        assert not ui.exception and ui.session_state["estimated"]
    before_estimates = empty_db.execute(
        "SELECT count(*) FROM reading_completion_estimates"
    ).fetchone()[0]
    next(b for b in ui.button if b.label == "Read article").click().run()
    assert not ui.exception
    assert ui.session_state["navigation"]["content_id"] != nav["content_id"]
    assert not ui.session_state["estimated"]
    # Old browser state is fenced when switching articles.
    with monkeypatch.context() as browser:
        browser.setattr(reading_progress, "render_progress", lambda *_: browser_evidence(nav, ""))
        ui.run()
    assert not ui.exception and not ui.session_state["estimated"]
    assert (
        empty_db.execute("SELECT count(*) FROM reading_completion_estimates").fetchone()[0]
        == before_estimates
    )
    # Corrections create checked versions and never overwrite the source record.
    correction = {
        "client_request_id": str(uuid4()),
        "base_version_id": article["metadata_version_id"],
        "reviewer_label": "editor-a",
        **article["metadata"],
        "title": "Corrected data title",
    }
    corrected_path = "/reviewer/articles/" + article["content_id"] + "/versions"
    corrected = client.post(corrected_path, json=correction)
    assert corrected.status_code == 201
    assert client.post(corrected_path, json=correction).status_code == 201
    new_version = corrected.json()["metadata_version_id"]
    assert new_version != article["metadata_version_id"]
    fresh_quality = client.get("/quality/" + new_version).json()["quality"]
    assert fresh_quality["metadata_version_id"] == new_version
    assert all(w["metadata_version_id"] == new_version for w in fresh_quality["warnings"])
    assert client.get("/quality/" + article["metadata_version_id"]).status_code == 409
    assert (
        empty_db.execute(
            "SELECT title,text FROM metadata_versions WHERE metadata_version_id=%s",
            (article["metadata_version_id"],),
        ).fetchone()
        == before
    )


def test_language_learning_preferences_and_identity_isolation(empty_db, reader_stack, monkeypatch):
    client, _, _ = reader_stack
    session = str(uuid4())
    historical = empty_db.execute("SELECT count(*) FROM interactions").fetchone()[0]

    def feed(person="0", selected_session=session):
        return client.get("/recommend/" + person, params={"session_id": selected_session}).json()

    first = feed()
    assert first["personalization"]["learned_languages"] == {}
    for item in ("1", "3"):
        nav = {
            "session_id": session,
            "person_id": "0",
            "content_id": item,
            "navigation_id": str(uuid4()),
            "client_request_id": str(uuid4()),
            "prefetch_id": str(uuid4()),
        }
        opened = client.post("/reading/open", json=nav)
        assert opened.status_code == 200
    learned = feed()
    assert learned["personalization"]["learned_languages"]["en"] > 1.9
    assert learned["items"][0]["article"]["metadata"]["lang"] == "en"
    assert any(i["signals"]["language_multiplier"] < 1 for i in learned["items"])
    # An estimate upgrades the same article's weight, without counting the view twice.
    with monkeypatch.context() as clock:
        later = (datetime.now(UTC) + timedelta(seconds=12)).isoformat()
        clock.setattr(s, "now", lambda: later)
        estimate = {k: nav[k] for k in ("session_id", "person_id", "content_id", "navigation_id")}
        estimate.update(
            client_request_id=str(uuid4()),
            active_seconds=10,
            end_visible=True,
            rule_version=ESTIMATE_RULE,
            metadata_version_id=opened.json()["article"]["metadata_version_id"],
        )
        assert client.post("/reading/estimate", json=estimate).status_code == 200
        assert 2.9 < feed()["personalization"]["learned_languages"]["en"] <= 3
    assert feed(selected_session=str(uuid4()))["personalization"]["learned_languages"]["en"] > 2.9
    assert feed(person="7")["personalization"]["learned_languages"] == {}
    assert (
        client.get("/feed", params={"session_id": str(uuid4())}).json()["personalization"][
            "learned_languages"
        ]
        == {}
    )
    preference = {
        "session_id": session,
        "person_id": "0",
        "client_request_id": str(uuid4()),
        "language": "pt",
    }
    assert client.put("/reader/preferences", json=preference).status_code == 200
    assert client.put("/reader/preferences", json=preference).status_code == 200
    assert (
        client.put("/reader/preferences", json={**preference, "language": "all"}).status_code == 409
    )
    assert (
        client.put(
            "/reader/preferences",
            json={**preference, "client_request_id": str(uuid4()), "language": "zz"},
        ).status_code
        == 422
    )
    assert {i["article"]["metadata"]["lang"] for i in feed()["items"]} == {"pt"}
    assert (
        client.get(
            "/reader/preferences", params={"session_id": str(uuid4()), "person_id": "0"}
        ).json()["language"]
        == "pt"
    )
    next_one = client.get("/similar/3", params={"session_id": session, "person_id": "0"}).json()
    assert all(i["article"]["metadata"]["lang"] == "pt" for i in next_one["items"])
    anon = {**preference, "person_id": None, "client_request_id": str(uuid4()), "language": "en"}
    assert client.put("/reader/preferences", json=anon).status_code == 200
    assert (
        client.get("/reader/preferences", params={"session_id": str(uuid4())}).json()["language"]
        == "auto"
    )
    assert empty_db.execute("SELECT count(*) FROM interactions").fetchone()[0] == historical


def test_visible_impressions_rotate_expire_and_reject_stale_batches(
    empty_db, reader_stack, monkeypatch
):
    client, _, _ = reader_stack
    session = str(uuid4())

    def feed():
        return client.get("/recommend/0", params={"session_id": session}).json()

    first = feed()
    target = first["items"][0]["content_id"]
    unseen = first["items"][1]["content_id"]

    def impression(delivery, item=target):
        return {
            "session_id": session,
            "person_id": "0",
            "feed_id": delivery["feed_id"],
            "content_ids": [item],
            "visible_ms": 1000,
        }

    assert (
        client.post("/feed/impressions", json={**impression(first), "visible_ms": 999}).status_code
        == 422
    )
    assert client.post("/feed/impressions", json=impression(first)).status_code == 200
    assert client.post("/feed/impressions", json=impression(first)).status_code == 200
    assert empty_db.execute("SELECT count(*) FROM feed_impressions").fetchone()[0] == 1
    second = feed()
    assert second["items"][0]["content_id"] == target  # One impression is neutral.
    assert client.post("/feed/impressions", json=impression(first, unseen)).status_code == 409
    assert (
        client.post(
            "/feed/impressions", json={**impression(second), "content_ids": ["10"]}
        ).status_code
        == 422
    )
    assert client.post("/feed/impressions", json=impression(second)).status_code == 200
    rotated = feed()
    assert rotated["items"][0]["content_id"] != target
    assert (
        next(i for i in rotated["items"] if i["content_id"] == target)["signals"][
            "exposure_multiplier"
        ]
        < 1
    )
    assert (
        empty_db.execute(
            "SELECT count(*) FROM feed_impressions WHERE content_id=%s", (unseen,)
        ).fetchone()[0]
        == 0
    )
    # Ignored articles remain eligible, and the cooldown expires.
    with monkeypatch.context() as clock:
        later = (datetime.now(UTC) + timedelta(days=8)).isoformat()
        clock.setattr(s, "now", lambda: later)
        assert feed()["items"][0]["content_id"] == target
    active = feed()
    client.post(
        "/reading/reset",
        json={"session_id": session, "person_id": "7", "navigation_id": str(uuid4())},
    )
    assert client.post("/feed/impressions", json=impression(active, unseen)).status_code == 409
    assert (
        client.post("/feed/impressions", json={**impression(active), "person_id": "7"}).status_code
        == 409
    )
    # Already saved receipts can be retried after navigation, without adding an exposure.
    assert client.post("/feed/impressions", json=impression(first)).status_code == 200
    assert empty_db.execute("SELECT count(*) FROM feed_impressions").fetchone()[0] == 2
    fresh = feed()
    empty_db.execute(
        "INSERT INTO live_catalog_events VALUES (%s,%s,'removed',clock_timestamp(),'test')",
        (uuid4(), unseen),
    )
    empty_db.commit()
    assert client.post("/feed/impressions", json=impression(fresh, unseen)).status_code == 409
    assert unseen not in {i["content_id"] for i in feed()["items"]}


def test_reader_language_selector_and_visibility_retry(empty_db, reader_stack, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from cmdl.ui import feed_visibility, live_client

    client, _, _ = reader_stack
    outage = {"active": True}

    def local_request(method, path, **kwargs):
        if path == "/feed/impressions" and outage["active"]:
            raise live_client.APIError("Temporary feedback outage")
        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise live_client.APIError(response.json()["message"])
        return response.json()

    monkeypatch.setattr(live_client, "request", local_request)
    monkeypatch.setattr(
        feed_visibility,
        "render_impressions",
        lambda feed: {"feed_id": feed["feed_id"], "content_ids": [feed["items"][0]["content_id"]]},
    )
    ui = AppTest.from_file(Path("src/cmdl/ui/app.py").resolve(), default_timeout=30).run()
    ui.sidebar.selectbox[0].set_value("New demo reader").run()
    assert not ui.exception and ui.session_state["impressions"]["pending"]
    outage["active"] = False
    ui.session_state["impressions"]["retry_at"] = 0
    ui.run()
    assert not ui.exception and not ui.session_state["impressions"]["pending"]
    count = empty_db.execute("SELECT count(*) FROM feed_impressions").fetchone()[0]
    ui.run()
    assert empty_db.execute("SELECT count(*) FROM feed_impressions").fetchone()[0] == count
    ui.sidebar.selectbox[1].set_value("English").run()
    assert not ui.exception
    assert all(i["article"]["metadata"]["lang"] == "en" for i in ui.session_state["feed"]["items"])
    next(b for b in ui.button if b.label == "Read article").click().run()
    assert not ui.exception and ui.session_state["opened"]["metadata"]["lang"] == "en"
