"""HTTP-adapter workflow rehearsals, not proof of the pending product API/storage."""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from cmdl.ui.client import examples

APP = Path("src/cmdl/ui/app.py").resolve()


class ContractAPI:
    """Controlled service with independent review state and lost-ack injection."""

    def __init__(self):
        self.fixtures = examples()
        self.ranking = deepcopy(self.fixtures["personalized"]["body"])
        self.quality = deepcopy(self.fixtures["quality_available"]["body"])
        self.reviews = {}
        self.posts = []
        self.fail_recommendation = False
        self.lose_ack = False
        self.mismatch_quality = False

    def handle(self, request):
        path = request.url.path
        context = self.ranking["context"]
        status = 200
        if path.startswith("/recommend/"):
            if self.fail_recommendation:
                self.fail_recommendation = False
                status, body = 503, self.fixtures["dependency_failure"]["body"]
            else:
                body = self.ranking
        elif path == "/quality/report":
            body = deepcopy(self.fixtures["quality_report_partial_coverage"]["body"])
            body["review_decision_counts"]["confirm"] = len(self.reviews)
        elif path.startswith("/articles/"):
            article = next(
                i["article"]
                for i in self.ranking["items"]
                if i["article"]["content_id"] == path.rsplit("/", 1)[1]
            )
            body = {"context": context, "article": article}
        elif request.method == "GET" and path.startswith("/quality/"):
            assert request.url.params["snapshot_id"] == context["snapshot_id"]
            version = path.rsplit("/", 1)[1]
            if version == self.quality["quality"]["metadata_version_id"]:
                body = deepcopy(self.quality)
                for warning in body["quality"]["warnings"]:
                    warning["reviews"] = [
                        r for r in self.reviews.values() if r["warning_id"] == warning["warning_id"]
                    ]
            else:
                body = deepcopy(self.fixtures["quality_unavailable"]["body"])
            if self.mismatch_quality:
                body["quality"]["metadata_version_id"] = str(uuid4())
        elif request.method == "POST" and path.endswith("/reviews"):
            import json

            payload = json.loads(request.content)
            self.posts.append(payload)
            key = payload["client_request_id"]
            warning_id = path.split("/")[-2]
            quality = self.quality["quality"]
            assert payload["snapshot_id"] == quality["snapshot_id"]
            assert payload["metadata_version_id"] == quality["metadata_version_id"]
            assert payload["quality_run_id"] == quality["quality_run_id"]
            assert warning_id in {w["warning_id"] for w in quality["warnings"]}
            if key not in self.reviews:
                status = 201
                self.reviews[key] = {
                    **payload,
                    "warning_id": warning_id,
                    "review_id": str(uuid4()),
                    "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            else:
                assert all(self.reviews[key][k] == v for k, v in payload.items())
            body = self.reviews[key]
            if self.lose_ack:
                self.lose_ack = False
                raise httpx.ReadTimeout("Simulated lost save acknowledgment", request=request)
        else:
            raise AssertionError(f"Unexpected request: {request.method} {path}")
        return httpx.Response(status, json=body)


@pytest.fixture
def api(monkeypatch):
    service = ContractAPI()
    with httpx.Client(transport=httpx.MockTransport(service.handle)) as transport:
        monkeypatch.setattr(httpx, "get", lambda url, **kwargs: transport.get(url, **kwargs))
        monkeypatch.setattr(httpx, "post", lambda url, **kwargs: transport.post(url, **kwargs))
        yield service


def live_reader():
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[1].set_value("Live API").run()
    return app


def live_reviewer(api):
    app = live_reader()
    app.sidebar.radio[0].set_value("Reviewer").run()
    article = api.ranking["items"][0]["article"]
    app.text_input[0].set_value(article["content_id"])
    app.text_input[1].set_value(article["metadata_version_id"]).run()
    assert not app.exception
    return app


def test_reader_service_order_missing_quality_failure_then_retry(api):
    # Use a nonnumeric ordering to catch client sorting or ID coercion.
    api.ranking["items"].reverse()
    for rank, item in enumerate(api.ranking["items"], 1):
        item["rank"] = rank
    app = live_reader()
    app.button[0].click().run()
    assert not app.exception
    expected = [i["article"]["metadata"]["title"] for i in api.ranking["items"]]
    assert [s.value for s in app.subheader[1:]] == [
        f"{index}. {title}" for index, title in enumerate(expected, 1)
    ]
    app.button[1].click().run()
    assert any("Quality unavailable" in e.value for e in app.info)
    assert not any("Overall:" in e.value for e in app.markdown)
    api.fail_recommendation = True
    app.button[0].click().run()
    assert app.error
    assert not any(s.value == "Your reading list" for s in app.subheader)
    app.button[0].click().run()
    assert not app.error
    assert not app.exception
    assert any(s.value == "Your reading list" for s in app.subheader)


def test_reader_rejects_quality_for_another_metadata_version(api):
    api.mismatch_quality = True
    app = live_reader()
    app.button[0].click().run()
    app.button[1].click().run()
    assert not app.exception
    assert any("does not match" in e.value for e in app.error)
    assert not any("Overall:" in e.value for e in app.markdown)


def test_review_lost_ack_replay_and_fresh_ui_session(api):
    original = deepcopy(api.ranking["items"][0]["article"])
    app = live_reviewer(api)
    api.lose_ack = True
    app.text_input[2].set_value(" reviewer-a ")
    app.button[0].click().run()
    assert not app.exception
    assert any("Could not verify the save" in e.value for e in app.error)
    assert len(api.reviews) == 1  # Server committed before transport failure.
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    assert len(api.reviews) == 1
    assert len(api.posts) == 2
    assert api.posts[0] == api.posts[1]
    assert api.posts[0]["reviewer_label"] == "reviewer-a"
    assert app.success[0].value == "Decision saved by the service."
    fresh = live_reviewer(api)
    assert any("reviewer-a" in e.value for e in fresh.markdown)
    assert api.ranking["items"][0]["article"] == original
    # A changed opinion creates a new event without overwriting the first.
    fresh.text_input[2].set_value("reviewer-a")
    fresh.radio[0].set_value("reject")
    fresh.button[0].click().run()
    assert not fresh.exception
    assert len(api.reviews) == 2
    assert {r["decision"] for r in api.reviews.values()} == {"confirm", "reject"}


def test_reviewer_version_conflict_disables_submission(api):
    api.mismatch_quality = True
    app = live_reviewer(api)
    assert app.error
    assert not any("Save decision" == b.label for b in app.button)
    assert not api.posts
