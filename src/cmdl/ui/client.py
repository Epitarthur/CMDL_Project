"""Reader adapters share the approved API payloads in example and live modes."""

import json
from copy import deepcopy
from pathlib import Path

import httpx

SCENARIOS = {
    "Personalized": "personalized",
    "New user · short results": "cold_start_short",
    "Mixed recommendations and fallback": "mixed_primary_and_fallback",
    "Empty catalog": "empty_catalog",
    "Service failure": "dependency_failure",
}


class ServiceError(ValueError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def examples():
    return json.loads(Path(__file__).with_name("examples.json").read_text())


class ReaderClient:
    def __init__(self, api_url=None, scenario="Personalized"):
        self.api_url = api_url
        self.scenario = scenario

    def get(self, path, params=None, fixture=None):
        if self.api_url is None:
            example = deepcopy(examples()[fixture])
            body, status = example["body"], example["http_status"]
        else:
            try:
                response = httpx.get(self.api_url.rstrip("/") + path, params=params, timeout=5)
                body, status = response.json(), response.status_code
            except (httpx.HTTPError, ValueError) as exc:
                raise ServiceError("Could not reach the service. Please try again.", True) from exc
        if status >= 400:
            raise ServiceError(body.get("message", "Request failed."), body.get("retryable", False))
        return body

    def recommend(self, person_id, limit):
        return self.get(
            f"/recommend/{person_id}",
            {"limit": limit},
            SCENARIOS[self.scenario],
        )

    def inspect(self, item, context):
        article = item["article"]
        if self.api_url is None:
            # The supplied ranking embeds exact metadata and its matching quality summary.
            return deepcopy(article), deepcopy(item["quality"])
        result = self.get(
            f"/articles/{article['content_id']}", {"snapshot_id": context["snapshot_id"]}
        )
        if result["context"]["snapshot_id"] != context["snapshot_id"]:
            raise ServiceError("The article snapshot changed. Refresh recommendations.")
        current = result["article"]
        if current["metadata_version_id"] != article["metadata_version_id"]:
            raise ServiceError("The article version changed. Refresh recommendations.")
        quality = self.get(
            f"/quality/{article['metadata_version_id']}",
            {"snapshot_id": context["snapshot_id"]},
        )["quality"]
        if (
            quality["snapshot_id"] != context["snapshot_id"]
            or quality["metadata_version_id"] != article["metadata_version_id"]
        ):
            raise ServiceError("The quality report does not match this article version.")
        return current, quality


class ReviewerClient(ReaderClient):
    def report(self, fixture="quality_report_partial_coverage"):
        return self.get("/quality/report", fixture=fixture)

    def quality(self, version, snapshot, fixture="quality_available"):
        result = self.get(f"/quality/{version}", {"snapshot_id": snapshot}, fixture=fixture)
        quality = result["quality"]
        if (
            result["context"]["snapshot_id"] != snapshot
            or quality["snapshot_id"] != snapshot
            or quality["metadata_version_id"] != version
        ):
            raise ServiceError("Quality does not match the selected snapshot and metadata version.")
        return quality

    def article(self, content_id, version, snapshot):
        result = self.get(f"/articles/{content_id}", {"snapshot_id": snapshot}, fixture="article")
        article = result["article"]
        if (
            result["context"]["snapshot_id"] != snapshot
            or article["content_id"] != content_id
            or article["metadata_version_id"] != version
        ):
            raise ServiceError("Article does not match the selected metadata version.")
        return article

    def submit(self, warning_id, payload):
        if self.api_url is None:
            raise ServiceError("Example decisions must remain in the browser session.")
        try:
            response = httpx.post(
                self.api_url.rstrip("/") + f"/quality/warnings/{warning_id}/reviews",
                json=payload,
                timeout=5,
            )
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError(
                "Could not verify the save. Retry the same decision safely.", True
            ) from exc
        if response.status_code not in (200, 201):
            raise ServiceError(body.get("message", "Save failed."), body.get("retryable", False))
        if body.get("warning_id") != warning_id or any(
            body.get(key) != value for key, value in payload.items()
        ):
            raise ServiceError("The saved response does not match the submitted review.")
        return body
