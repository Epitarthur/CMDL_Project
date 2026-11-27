from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cmdl.data.temporal import ArticleAtCutoff, Metadata, PublishedSnapshot
from cmdl.recommender.models import Ranker


@pytest.fixture
def ranker():
    cutoff = datetime(2017, 1, 1, tzinfo=UTC)
    articles = []
    for content_id, text in (
        ("0", "cloud networks infrastructure security"),
        ("1", "kitchen recipes baking bread"),
        ("2", "cloud networks infrastructure monitoring"),
        ("3", "kitchen recipes baking cakes"),
    ):
        metadata = Metadata(
            uuid4(),
            content_id,
            uuid4(),
            datetime(2016, 1, 1, tzinfo=UTC),
            text,
            text,
            f"https://example.com/{content_id}",
            "en",
            "HTML",
            "a" * 64,
        )
        articles.append(ArticleAtCutoff(content_id, "available", metadata))
    return Ranker(
        PublishedSnapshot(
            uuid4(), "deskdrop-temporal-v1", cutoff, "original", tuple(articles), (), frozenset()
        )
    )


def test_anonymous_session_history_personalizes_feed_and_next_article(ranker):
    assert ranker.rank(mode="tfidf")[0]["source"] == "popularity"

    feed = ranker.rank(recent=["1"], mode="tfidf")
    assert feed[0]["content_id"] == "3"
    assert feed[0]["explanation"]["text"] == "Text similarity to your reading history."
    assert {item["content_id"] for item in feed} == {"0", "2", "3"}

    next_items = ranker.rank(current="0", recent=["1", "0"], mode="tfidf")
    assert {item["content_id"] for item in next_items} == {"2", "3"}
    assert all(item["signals"]["tfidf"] > 0 for item in next_items)
    assert all(
        "this article and your reading history" in i["explanation"]["text"] for i in next_items
    )


@pytest.mark.parametrize("person", [None, "reader"])
def test_repeated_events_and_current_article_do_not_duplicate_context(ranker, person):
    original = ranker.rank(person, current="0", recent=["1"], mode="tfidf")
    assert all(item["signals"]["tfidf"] > 0 for item in original)
    for recent in (["1", "0"], ["1", "1"]):
        assert ranker.rank(person, current="0", recent=recent, mode="tfidf") == original
