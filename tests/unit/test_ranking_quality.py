from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from cmdl.data.temporal import ArticleAtCutoff, Metadata, PublishedSnapshot
from cmdl.evaluation.runner import corrupt, metrics
from cmdl.quality.checks import assess
from cmdl.recommender.models import Ranker, five_core, implicit_als
from cmdl.ui.live_client import matches


def test_quality_uncertainty_and_field_warnings_survive_good_overall():
    result = assess(
        {
            "metadata_version_id": str(uuid4()),
            "title": "",
            "text": "short",
            "url": "javascript:alert(1)",
            "lang": "zz",
        }
    )
    assert result["overall_score"] is None
    assert result["content_reliability"] < 0.3
    assert {w["field"] for w in result["warnings"]} >= {"title", "text", "url", "lang"}
    assert all(
        w["metadata_version_id"] == result["metadata_version_id"] for w in result["warnings"]
    )


def test_five_core_uses_distinct_training_partners():
    pairs = {(str(u), str(i)): 100 for u in range(5) for i in range(5)}
    pairs["new", "0"] = 1000
    pairs["0", "new-item"] = 1000
    assert len(five_core(pairs)) == 25


def test_metric_hand_calculation_and_navigation_fences():
    result = metrics(["a", "b", "c"], {"a", "c"})
    assert result["recall_at_10"] == 1
    assert np.isclose(result["ndcg_at_10"], 1.5 / (1 + 1 / np.log2(3)))
    nav = {"navigation_id": "new", "content_id": "1", "person_id": None}
    assert matches(dict(nav), nav)
    assert not matches({**nav, "navigation_id": "old"}, nav)
    assert not matches({**nav, "person_id": "2"}, nav)


def test_content_only_discovery_and_empty_catalog():
    cutoff = datetime(2017, 1, 1, tzinfo=UTC)
    articles = []
    for i, text in enumerate(
        (
            "data science machine learning systems",
            "data science machine learning article",
            "cooking recipes fresh food",
        )
    ):
        metadata = Metadata(
            uuid4(),
            str(i),
            uuid4(),
            datetime(2016, 1, 1, tzinfo=UTC),
            text,
            text,
            "https://example.com",
            "en",
            "HTML",
            "a" * 64,
        )
        articles.append(ArticleAtCutoff(str(i), "available", metadata))
    snapshot = PublishedSnapshot(
        uuid4(), "deskdrop-temporal-v1", cutoff, "original", tuple(articles), (), frozenset()
    )
    ranker = Ranker(snapshot)
    assert ranker.rank(current="0", mode="tfidf", limit=1)[0]["content_id"] == "1"
    assert ranker.rank(current="0", excluded={"1", "2"}) == []
    empty = PublishedSnapshot(
        uuid4(), "deskdrop-temporal-v1", cutoff, "original", (), (), frozenset()
    )
    assert Ranker(empty).rank() == []
    changed, selected = corrupt(ranker.records, "title", 0.5, 42)
    assert len(selected) == 1
    for key, record in changed.items():
        assert record["text"] == ranker.records[key]["text"]


def test_implicit_als_is_seeded_and_recovers_preferences():
    from scipy.sparse import csr_matrix

    matrix = csr_matrix(np.array([[3.0, 2.0, 0.0], [2.0, 3.0, 0.0], [0.0, 0.0, 4.0]]))
    u, i = implicit_als(matrix, factors=4, iterations=15)
    u2, i2 = implicit_als(matrix, factors=4, iterations=15)
    assert np.allclose(u, u2) and np.allclose(i, i2)
    assert (u @ i.T)[0, 1] > (u @ i.T)[0, 2]
