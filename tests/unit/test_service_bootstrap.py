"""Ensure development health never overstates application readiness."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from cmdl.api import app as service


def test_connected_bootstrap_remains_not_ready(monkeypatch):
    monkeypatch.setattr(service, "database_available", lambda: True)
    with TestClient(service.app) as client:
        assert client.get("/health").json() == {"status": "healthy", "database": "connected"}
        response = client.get("/ready")
    assert response.status_code == 503
    spec = json.loads(Path("docs/api/openapi.json").read_text())
    Draft202012Validator(
        {"$ref": "#/components/schemas/Error", "components": spec["components"]},
        format_checker=FormatChecker(),
    ).validate(response.json())


def test_database_outage(monkeypatch):
    monkeypatch.setattr(service, "database_available", lambda: False)
    with TestClient(service.app) as client:
        for route in ("/health", "/ready"):
            response = client.get(route)
            assert response.status_code == 503
            assert response.json()["code"] == "DependencyUnavailable"
            assert response.headers["Retry-After"] == "5"
