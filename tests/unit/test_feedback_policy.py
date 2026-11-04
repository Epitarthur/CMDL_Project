import pytest

from cmdl.recommender.feedback_policy import adjust, exposure_strength, language_strength


def item(content_id, score=1):
    return {
        "content_id": content_id,
        "score": score,
        "signals": {},
        "explanation": {"code": "popularity", "text": "Popular."},
    }


def test_language_learning_is_soft_decays_and_can_be_disabled_or_filtered():
    ranked = [item("p"), item("e", 0.9)]
    catalog = {"e": {"lang": "en"}, "p": {"lang": "pt"}}
    languages = language_strength([{"lang": "en", "weight": 2, "age_seconds": 0}])
    auto = adjust(ranked, catalog, "auto", languages, {})
    assert auto[0]["content_id"] == "e"
    assert len(auto) == 2
    assert auto[1]["signals"]["language_multiplier"] == pytest.approx(0.5)
    assert adjust(ranked, catalog, "all", languages, {})[0]["content_id"] == "p"
    assert [r["content_id"] for r in adjust(ranked, catalog, "en", languages, {})] == ["e"]
    assert adjust(ranked, catalog, "es", languages, {}) == []
    assert language_strength([{"lang": "en", "weight": 2, "age_seconds": 604800}])["en"] == 1
    assert language_strength([{"lang": "en", "weight": 2, "age_seconds": 31 * 86400}]) == {}


def test_repeated_exposure_rotates_items_but_one_impression_does_not():
    ranked = [item("a"), item("b", 0.8)]
    catalog = {i: {"lang": "en"} for i in ("a", "b")}
    assert adjust(ranked, catalog, "auto", {}, {"a": 1})[0]["content_id"] == "a"
    rotated = adjust(ranked, catalog, "auto", {}, {"a": 2})
    assert rotated[0]["content_id"] == "b"
    assert rotated[1]["signals"]["exposure_multiplier"] == pytest.approx(0.4)
    assert len(rotated) == 2  # Not hidden or marked consumed.
    assert exposure_strength([{"content_id": "a", "age_seconds": 86400}])["a"] == 0.5
    assert exposure_strength([{"content_id": "a", "age_seconds": 8 * 86400}]) == {}
