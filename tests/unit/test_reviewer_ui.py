"""Reviewer version selection and decision history remain observable and explicit."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path("src/cmdl/ui/app.py").resolve()


def reviewer():
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value("Reviewer").run()
    assert not app.exception
    return app


def test_example_review_validation_history_and_source_preservation():
    app = reviewer()
    original = app.json[0].value
    app.button[0].click().run()
    assert any("nonblank" in e.value for e in app.error)
    app.text_input[0].set_value(" reviewer-a ")
    app.button[0].click().run()
    assert not app.exception
    assert "session only" in app.success[0].value
    assert any("reviewer-a" in e.value for e in app.markdown)
    assert app.json[0].value == original
    # Repeated submission retains the idempotency key and one history event.
    app.button[0].click().run()
    assert len(app.session_state["example_reviews"][app.selectbox[2].value]) == 1
    app.button[1].click().run()
    assert any("reviewer-a" in e.value for e in app.markdown)
    app.selectbox[1].select("Quality unavailable").run()
    assert not app.exception
    assert not app.button


def test_partial_and_missing_collection_assessments():
    app = reviewer()
    app.selectbox[1].select("Partial assessment").run()
    assert not app.exception
    assert any("Partially assessed" in e.value for e in app.info)
    app.selectbox[0].select("quality_report_no_run").run()
    assert not app.exception
    assert any("Collection quality unavailable" in e.value for e in app.info)
