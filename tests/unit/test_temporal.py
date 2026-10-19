"""Approved chronological examples and the shared model input boundary."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from cmdl.data.temporal import (
    TEST_END,
    TRAIN_END,
    VALIDATION_END,
    ArticleAtCutoff,
    LifecycleEvent,
    Metadata,
    PublishedSnapshot,
    SourceInteraction,
    TemporalError,
    TrainingInteraction,
    _Timeline,
    partition,
    utc_boundary,
)


def at(second):
    return datetime.fromtimestamp(second, UTC)


def event(second, kind, row, content="10"):
    return LifecycleEvent(UUID(int=row), content, at(second), kind, row)


def version(e, title=None):
    return Metadata(
        uuid4(),
        e.content_id,
        e.article_event_id,
        e.event_at,
        title or str(e.row_number),
        "text",
        "https://example.org",
        "en",
        "HTML",
        str(e.row_number),
    )


@pytest.fixture
def timeline():
    events = [
        event(10, "CONTENT SHARED", 1),
        event(20, "CONTENT REMOVED", 2),
        event(30, "CONTENT SHARED", 3),
        event(5, "CONTENT REMOVED", 4, "2"),
    ]
    return _Timeline(
        events, {e.article_event_id: version(e) for e in events if e.event_type == "CONTENT SHARED"}
    )


@pytest.mark.parametrize(
    "second,state,metadata_row",
    [
        (9, "not_shared", None),
        (10, "ambiguous", None),
        (15, "available", 1),
        (20, "ambiguous", 1),
        (25, "removed", 1),
        (30, "ambiguous", 1),
        (31, "available", 3),
    ],
)
def test_approved_share_remove_reshare_request_examples(timeline, second, state, metadata_row):
    assert timeline.availability("10", at(second)) == state
    metadata = timeline.metadata_at("10", at(second))
    assert (metadata.share_event_id.int if metadata else None) == metadata_row
    if metadata:
        assert metadata.shared_at < at(second)


@pytest.mark.parametrize(
    "second,content,reason",
    [
        (9, "10", "pre_share"),
        (10, "10", "ambiguous"),
        (15, "10", None),
        (20, "10", "ambiguous"),
        (25, "10", "removed"),
        (30, "10", "ambiguous"),
        (31, "10", None),
        (2, "2", "removal_only"),
        (40, "3", "unresolved"),
    ],
)
def test_interaction_precedence(timeline, second, content, reason):
    interaction = SourceInteraction(uuid4(), "1", content, "VIEW", at(second), 1)
    assert timeline.exclusion(interaction) == reason


@pytest.mark.parametrize(
    "boundary,prior,current",
    [
        (TRAIN_END, "train", "validation"),
        (VALIDATION_END, "validation", "test"),
        (TEST_END, "test", "after_test"),
    ],
)
def test_half_open_split_boundaries(boundary, prior, current):
    assert partition(boundary - timedelta(microseconds=1)) == prior
    assert partition(boundary) == current
    assert partition(boundary + timedelta(microseconds=1)) == current


def test_timezone_offsets_and_naive_rejection():
    assert utc_boundary(datetime(2017, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))) == TRAIN_END
    with pytest.raises(TemporalError, match="timezone"):
        partition(datetime(2017, 1, 1))


def test_conflicting_lifecycle_stays_ambiguous_until_later_event():
    events = [
        event(10, "CONTENT SHARED", 1),
        event(20, "CONTENT SHARED", 2),
        event(20, "CONTENT REMOVED", 3),
        event(30, "CONTENT SHARED", 4),
    ]
    timeline = _Timeline(
        events, {e.article_event_id: version(e) for e in events if e.event_type == "CONTENT SHARED"}
    )
    assert timeline.availability("10", at(25)) == "ambiguous"
    assert timeline.metadata_at("10", at(25)).share_event_id == UUID(int=1)
    assert timeline.availability("10", at(31)) == "available"
    assert timeline.metadata_at("10", at(31)).share_event_id == UUID(int=4)


def test_same_second_distinct_metadata_fails_closed():
    events = [event(10, "CONTENT SHARED", 1), event(10, "CONTENT SHARED", 2)]
    timeline = _Timeline(events, {e.article_event_id: version(e) for e in events})
    with pytest.raises(TemporalError, match="no known order"):
        timeline.metadata_at("10", at(11))


def test_shared_candidate_policy_removal_consumption_and_unknown_user(timeline):
    articles = tuple(
        ArticleAtCutoff(
            item, timeline.availability(item, at(31)), timeline.metadata_at(item, at(31))
        )
        for item in ("10", "2", "3")
    )
    metadata = timeline.metadata_at("10", at(15))
    history = (TrainingInteraction(uuid4(), "1", "10", "LIKE", at(12), metadata),)
    snapshot = PublishedSnapshot(
        uuid4(),
        "deskdrop-temporal-v1",
        at(31),
        "original",
        articles,
        history,
        frozenset({("1", "10")}),
    )
    assert [a.content_id for a in snapshot.candidates()] == ["10"]
    assert snapshot.candidates("1") == ()
    assert [a.content_id for a in snapshot.candidates("999")] == ["10"]
    assert snapshot.candidates(reference_content_id="10") == ()
    assert snapshot.user_history("1")[0].metadata.share_event_id == UUID(int=1)
    assert snapshot.known_users == frozenset({"1"})
    with pytest.raises(TemporalError):
        snapshot.candidates("9223372036854775807.0")
    with pytest.raises(TemporalError):
        snapshot.candidates(reference_content_id="2")


def test_model_view_rejects_future_metadata_and_history(timeline):
    current = timeline.metadata_at("10", at(31))
    with pytest.raises(TemporalError, match="request boundary"):
        PublishedSnapshot(
            uuid4(),
            "deskdrop-temporal-v1",
            at(30),
            "original",
            (ArticleAtCutoff("10", "available", current),),
            (),
            frozenset(),
        )
    with pytest.raises(TemporalError, match="exclusive cutoff"):
        PublishedSnapshot(
            uuid4(),
            "deskdrop-temporal-v1",
            at(31),
            "original",
            (),
            (TrainingInteraction(uuid4(), "1", "10", "VIEW", at(31), current),),
            frozenset(),
        )
