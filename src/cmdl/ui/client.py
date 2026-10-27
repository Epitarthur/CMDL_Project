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
