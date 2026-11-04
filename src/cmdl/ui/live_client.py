"HTTP transport and navigation matching; no background task mutates Streamlit state."

import os

import httpx


class APIError(ValueError):
    pass


def request(method, path, **kwargs):
    try:
        response = httpx.request(
            method,
            os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/") + path,
            timeout=15,
            **kwargs,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise APIError("Could not reach the service. Retry your last action.") from exc
    if response.is_error:
        raise APIError(body.get("message", body.get("detail", "Request failed.")))
    return body


def matches(response, navigation):
    return all(
        response.get(k) == navigation.get(k) for k in ("navigation_id", "content_id", "person_id")
    )
