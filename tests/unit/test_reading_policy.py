from cmdl.reading import required_seconds


def test_length_based_dwell_has_short_article_floor_and_long_article_cap():
    assert required_seconds("") == 10
    assert required_seconds("palavra " * 80) == 10
    assert required_seconds("word " * 81) == 11
    assert required_seconds("word " * 480) == 60
    assert required_seconds("word " * 10000) == 120
