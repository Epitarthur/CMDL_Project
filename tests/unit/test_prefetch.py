from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from cmdl import feedback, serving


@pytest.fixture
def prefetch(monkeypatch):
    at = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(serving, "now", at.isoformat)
    release = {"release_id": uuid4(), "snapshot_id": uuid4()}
    removed, heads, recent = set(), {}, []
    preferences = {"version": "v1", "language": "auto", "active": False, "inputs": ([], [])}
    ranker = SimpleNamespace(cutoff="2017-03-01T00:00:00Z")
    inputs = (release, ranker, removed, heads, recent, preferences)
    monkeypatch.setattr(serving, "recommendation_inputs", lambda *args: inputs)
    quality = Mock(return_value={"review": "fresh"})
    monkeypatch.setattr(serving, "quality", quality)
    ranking = Mock(
        side_effect=lambda *args, **kwargs: {
            "context": serving.context(release, ranker),
            "items": [{"article": {"metadata_version_id": "123"}, "quality": {"review": "old"}}],
        }
    )
    monkeypatch.setattr(serving, "recommend", ranking)
    row = {"request_id": uuid4(), "person_id": None, "session_id": uuid4(), "content_id": "1"}
    connection = Mock()

    def save_result(_query, params):
        row["result"] = deepcopy(params[0].obj)

    connection.execute.side_effect = save_result
    row["result"] = None
    serving.next_recommendation(connection, row)
    return connection, row, at, inputs, ranking, quality


def test_prefetch_reuses_ranking_and_refreshes_review_details(prefetch):
    connection, row, _, _, ranking, quality = prefetch
    result = serving.next_recommendation(connection, row)

    assert result["items"][0]["quality"] == {"review": "fresh"}
    quality.assert_called_once()
    ranking.assert_called_once()
    connection.execute.assert_called_once()


@pytest.mark.parametrize(
    "change",
    ["release", "removal", "correction", "read", "language", "impression", "expiry", "legacy"],
)
def test_stale_prefetch_reranks_once_then_reuses_result(prefetch, monkeypatch, change):
    connection, row, at, inputs, ranking, _ = prefetch
    release, _, removed, heads, recent, preferences = inputs
    if change == "release":
        release["release_id"] = uuid4()
    elif change == "removal":
        removed.add("2")
    elif change == "correction":
        heads["2"] = str(uuid4())
    elif change == "read":
        recent.append("2")
    elif change == "language":
        preferences["language"] = "en"
    elif change == "impression":
        preferences["inputs"][1].append(str(uuid4()))
    elif change == "expiry":
        monkeypatch.setattr(
            serving, "now", (at + timedelta(seconds=serving.PREFETCH_MAX_AGE_SECONDS)).isoformat
        )
    else:
        row["result"] = row["result"]["response"]

    serving.next_recommendation(connection, row)
    serving.next_recommendation(connection, row)

    assert ranking.call_count == connection.execute.call_count == 2


def test_empty_prefetch_is_reused_until_the_catalog_changes(prefetch):
    connection, row, _, inputs, ranking, quality = prefetch
    row["result"]["response"]["items"] = []
    assert serving.next_recommendation(connection, row)["items"] == []
    ranking.assert_called_once()
    quality.assert_not_called()

    inputs[2].add("3")
    assert serving.next_recommendation(connection, row)["items"]
    assert ranking.call_count == 2


def test_feedback_revision_tracks_receipts_without_changing_on_each_poll():
    connection = Mock()
    read_id, older, newer = uuid4(), uuid4(), uuid4()
    session = uuid4()

    def policy(age, weight):
        reading = {"client_request_id": read_id, "lang": "en", "weight": weight, "age_seconds": age}
        impressions = [
            {"impression_id": event, "content_id": "2", "age_seconds": age}
            for event in (older, newer)
        ]
        connection.execute.side_effect = [
            SimpleNamespace(fetchone=lambda: None),
            SimpleNamespace(fetchall=lambda: [reading]),
            SimpleNamespace(fetchall=lambda: impressions),
        ]
        return feedback.policy(connection, None, session, "2026-01-01T00:00:00Z")

    first = policy(5, 1)
    later = policy(10, 1)
    completed = policy(10, 2)
    assert first["inputs"] == later["inputs"]
    assert first["learned_languages"]["en"] > later["learned_languages"]["en"]
    assert completed["inputs"] != later["inputs"]


def test_correction_duplicates_use_only_current_versions_of_available_peers():
    old = {"title": "Old title", "url": "https://example.com/old"}
    current = {"title": "Live title", "url": "https://example.com/live"}
    unavailable = {"title": "Removed title", "url": "https://example.com/removed"}
    ranker = SimpleNamespace(catalog={"1": old, "2": old, "3": unavailable})
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [current]
    heads = {"1": str(uuid4()), "2": str(uuid4())}

    counts = serving.correction_duplicates(
        connection, ranker, "1", {"title": " LIVE TITLE ", "url": " "}, {"3"}, heads
    )

    assert counts == {"title": {"live title": 2}}
    assert connection.execute.call_args.args[1] == ([heads["2"]],)
