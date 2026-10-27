"""Observable reader flows before live product services are available."""

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from cmdl.ui.client import examples

APP = Path("src/cmdl/ui/app.py").resolve()


def test_packaged_examples_match_approved_contract():
    approved = json.loads(Path("docs/api/examples.json").read_text())
    assert examples() == {e["name"]: e for e in approved["examples"]}


def test_reader_order_inspection_and_failure_recovery():
    app = AppTest.from_file(APP).run()
    assert not app.exception
    app.button[0].click().run()
    assert not app.exception
    assert "1. Illustrative article" in app.subheader[1].value
    app.button[1].click().run()
    assert not app.exception
    assert any("Overall: 75%" in x.value for x in app.markdown)
    app.selectbox[0].select("Service failure").run()
    assert not any("Your reading list" in x.value for x in app.subheader)
    app.button[0].click().run()
    assert app.error
    assert not any("Your reading list" in x.value for x in app.subheader)
    app.selectbox[0].select("Empty catalog").run()
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    assert any("No eligible articles" in x.value for x in app.markdown)


def test_short_result_and_unavailable_quality():
    app = AppTest.from_file(APP).run()
    app.selectbox[0].select("New user · short results").run()
    app.button[0].click().run()
    assert not app.exception
    assert any("Only" in x.value for x in app.info)
    app.button[1].click().run()
    assert not app.exception


def test_live_inspection_rejects_mismatched_versions(monkeypatch):
    import pytest

    from cmdl.ui.client import ReaderClient, ServiceError

    ranking = examples()["personalized"]["body"]
    item = ranking["items"][0]
    client = ReaderClient("http://unused")
    wrong = {"context": ranking["context"], "article": dict(item["article"])}
    wrong["article"]["metadata_version_id"] = "different-version"
    monkeypatch.setattr(client, "get", lambda *args, **kwargs: wrong)
    with pytest.raises(ServiceError, match="version changed"):
        client.inspect(item, ranking["context"])
