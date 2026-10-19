"""Validate the design contract and fixture semantics; no running API is claimed."""

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "docs/api/openapi.json").read_text())
BUNDLE = json.loads((ROOT / "docs/api/examples.json").read_text())
EXAMPLES = BUNDLE["examples"]
LINEAGE = BUNDLE["lineage"]
BY_NAME = {example["name"]: example for example in EXAMPLES}
OPERATIONS = {
    operation["operationId"]: operation
    for path in SPEC["paths"].values()
    for operation in path.values()
}


def validator(schema):
    return Draft202012Validator(
        {**schema, "components": SPEC["components"]}, format_checker=FormatChecker()
    )


def schema_for(example):
    return OPERATIONS[example["operation_id"]]["responses"][str(example["http_status"])]["content"][
        "application/json"
    ]["schema"]


def assert_quality(quality, context):
    assert quality["snapshot_id"] == context["snapshot_id"]
    metadata = LINEAGE["metadata_versions"][quality["metadata_version_id"]]
    assert metadata["snapshot_id"] == context["snapshot_id"]
    if quality["status"] == "unavailable":
        assert quality["overall_score"] is None and quality["dimensions"] is None
        assert quality["warnings"] == []
        return
    run = LINEAGE["quality_runs"][quality["quality_run_id"]]
    assert run["snapshot_id"] == context["snapshot_id"]
    assert run["checks_version"] == quality["checks_version"]
    values = []
    fully_assessed = True
    for dimension in quality["dimensions"].values():
        assessed, passed = dimension["assessed_checks"], dimension["passed_checks"]
        assert passed <= assessed
        if assessed:
            assert dimension["score"] == pytest.approx(passed / assessed)
            values.append(dimension["score"])
        else:
            assert dimension["score"] is None and passed == 0
        fully_assessed &= dimension["unassessed_checks"] == 0
    if len(values) == 4 and fully_assessed:
        assert quality["overall_score"] == pytest.approx(sum(values) / 4)
    else:
        assert quality["overall_score"] is None
    assert len({warning["warning_id"] for warning in quality["warnings"]}) == len(
        quality["warnings"]
    )
    for warning in quality["warnings"]:
        reviews = warning["reviews"]
        assert reviews == sorted(reviews, key=lambda r: (r["created_at"], r["review_id"]))
        for review in reviews:
            assert review["warning_id"] == warning["warning_id"]
            for field in ["metadata_version_id", "quality_run_id", "snapshot_id"]:
                assert review[field] == quality[field]


def assert_semantics(body):
    if "context" not in body:
        return
    context = body["context"]
    snapshot = LINEAGE["snapshots"][context["snapshot_id"]]
    assert snapshot["as_of_at"] == context["as_of_at"]
    assert snapshot["protocol_id"] == context["protocol_id"]
    if "items" in body:
        if body["ranking_set_id"] is not None:
            ranking = LINEAGE["ranking_sets"][body["ranking_set_id"]]
            assert ranking["snapshot_id"] == context["snapshot_id"]
            assert ranking["model_run_id"] == body["primary_model_run_id"]
            assert ranking["kind"] == ("similar" if "content_id" in body else "user")
        items = body["items"]
        assert body["returned_count"] == len(items) <= body["limit"]
        assert body["short_result"] == (len(items) < body["limit"])
        assert [item["rank"] for item in items] == list(range(1, len(items) + 1))
        assert len({item["article"]["content_id"] for item in items}) == len(items)
        for item in items:
            article = item["article"]
            assert article["availability"] == "available"
            metadata = LINEAGE["metadata_versions"][article["metadata_version_id"]]
            assert metadata["content_id"] == article["content_id"]
            assert metadata["snapshot_id"] == context["snapshot_id"]
            model = LINEAGE["model_runs"][item["model_run_id"]]
            assert model["snapshot_id"] == context["snapshot_id"]
            assert model["feature_set_id"] == item["feature_set_id"]
            if item["feature_set_id"] is not None:
                feature = LINEAGE["feature_sets"][item["feature_set_id"]]
                assert feature["snapshot_id"] == context["snapshot_id"]
            assert item["quality"]["metadata_version_id"] == article["metadata_version_id"]
            assert (
                item["model_run_id"]
                == body[
                    "primary_model_run_id"
                    if item["source"] == "primary"
                    else "fallback_model_run_id"
                ]
            )
            if item["source"] == "popularity":
                assert item["feature_set_id"] is None
                assert item["explanation"]["code"] == "popular"
            assert datetime.fromisoformat(article["metadata"]["share_event_at"]) < (
                datetime.fromisoformat(context["as_of_at"])
            )
            if "content_id" in body:
                assert article["content_id"] != body["content_id"]
            assert_quality(item["quality"], context)
    if "quality" in body:
        assert_quality(body["quality"], context)
    if "catalog_records" in body:
        assert body["assessed_records"] + body["unassessed_records"] == body["catalog_records"]
        for name, denominator in body["dimension_denominators"].items():
            assert denominator <= body["assessed_records"]
            assert (body["dimension_means"][name] is None) == (denominator == 0)


def test_openapi_document_is_valid():
    validate(SPEC)
    for schema in SPEC["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: example["name"])
def test_examples_match_response_and_request_contracts(example):
    validator(schema_for(example)).validate(example["body"])
    response = OPERATIONS[example["operation_id"]]["responses"][str(example["http_status"])]
    assert (
        response["content"]["application/json"]["examples"][example["name"]]["value"]
        == example["body"]
    )
    if "request" in example:
        request_schema = OPERATIONS[example["operation_id"]]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        validator(request_schema).validate(example["request"])
    if example["http_status"] < 400:
        assert_semantics(example["body"])


@pytest.mark.parametrize(
    ("schema_name", "value"),
    [
        ("SourceId", 9223372036854775807),
        ("SourceId", "9.223372036854776e18"),
        ("SourceId", " 123"),
        ("SourceId", "123\n"),
        ("UtcTimestamp", "2017-02-01T00:00:00+08:00"),
        ("UtcTimestamp", "2017-02-30T00:00:00Z"),
        ("Uuid", "not-a-uuid"),
    ],
)
def test_invalid_identifiers_and_times_are_rejected(schema_name, value):
    assert not validator({"$ref": f"#/components/schemas/{schema_name}"}).is_valid(value)


@pytest.mark.parametrize("field,value", [("limit", 0), ("limit", 11), ("limit", 1.5)])
def test_limit_is_not_silently_clamped(field, value):
    parameters = OPERATIONS["recommend"]["parameters"]
    limit_schema = next(
        parameter["schema"] for parameter in parameters if parameter["name"] == field
    )
    assert not validator(limit_schema).is_valid(value)


@pytest.mark.parametrize("change", ["blank_reviewer", "unknown_decision", "long_comment", "extra"])
def test_invalid_review_payloads_are_rejected(change):
    request = copy.deepcopy(BY_NAME["review_created"]["request"])
    if change == "blank_reviewer":
        request["reviewer_label"] = "   "
    elif change == "unknown_decision":
        request["decision"] = "edit_original"
    elif change == "long_comment":
        request["comment"] = "x" * 1001
    else:
        request["source_text"] = "overwrite"
    assert not validator({"$ref": "#/components/schemas/ReviewRequest"}).is_valid(request)


def test_unavailable_quality_cannot_contain_a_good_score():
    body = copy.deepcopy(BY_NAME["quality_unavailable"]["body"])
    body["quality"]["overall_score"] = 1
    assert not validator(schema_for(BY_NAME["quality_unavailable"])).is_valid(body)


def test_idempotent_replay_keeps_original_server_identity():
    assert BY_NAME["review_created"]["body"] == BY_NAME["review_replayed"]["body"]
    assert BY_NAME["review_created"]["request"] == BY_NAME["review_replayed"]["request"]


@pytest.mark.parametrize(
    "change",
    ["wrong_version", "wrong_snapshot", "wrong_model", "bad_order", "future", "partial_good_score"],
)
def test_fixture_semantic_audit_detects_cross_field_mistakes(change):
    # JSON Schema cannot express database joins or arbitrary cross-field equality.
    # Audit fixtures here; runtime enforcement belongs to T06/T21.
    body = copy.deepcopy(BY_NAME["personalized"]["body"])
    item = body["items"][0]
    if change == "wrong_version":
        item["quality"]["metadata_version_id"] = "00000000-0000-4000-8000-000000000099"
    elif change == "wrong_snapshot":
        item["quality"]["snapshot_id"] = "00000000-0000-4000-8000-000000000099"
    elif change == "wrong_model":
        item["model_run_id"] = "00000000-0000-4000-8000-000000000051"
    elif change == "bad_order":
        item["rank"] = 2
    elif change == "future":
        item["article"]["metadata"]["share_event_at"] = "2017-02-01T00:00:00Z"
    else:
        item["quality"]["dimensions"]["consistency"]["unassessed_checks"] = 1
    with pytest.raises(AssertionError):
        assert_semantics(body)


def test_recommendation_schema_rejects_unavailable_articles():
    body = copy.deepcopy(BY_NAME["personalized"]["body"])
    body["items"][0]["article"]["availability"] = "removed"
    assert not validator(schema_for(BY_NAME["personalized"])).is_valid(body)
