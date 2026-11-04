"""CMDL live API v2; historical benchmark data is immutable."""

import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cmdl import feedback
from cmdl import serving as s
from cmdl.recommender.models import MODELS

app = FastAPI(title="CMDL Article Recommender", version="2.0.0")
ID = Annotated[str, Field(pattern=r"^-?(0|[1-9][0-9]*)$", max_length=100)]
Limit = Annotated[int, Query(ge=1, le=10)]


class Navigation(BaseModel):
    session_id: UUID
    person_id: ID | None = None
    content_id: ID
    navigation_id: UUID


class OpenArticle(Navigation):
    client_request_id: UUID
    prefetch_id: UUID


class Completion(Navigation):
    client_request_id: UUID
    metadata_version_id: UUID


class EstimatedCompletion(Completion):
    active_seconds: Annotated[float, Field(ge=0, le=86400, allow_inf_nan=False)]
    end_visible: bool
    rule_version: Literal["active-dwell-bottom-v1"]


class NextRequest(Navigation):
    prefetch_id: UUID


class Reset(BaseModel):
    session_id: UUID
    person_id: ID | None = None
    navigation_id: UUID


class ReaderPreference(BaseModel):
    session_id: UUID
    person_id: ID | None = None
    client_request_id: UUID
    language: Annotated[str, Field(pattern=r"^(auto|all|[a-z]{2})$")]


class FeedImpressions(BaseModel):
    session_id: UUID
    person_id: ID | None = None
    feed_id: UUID
    content_ids: Annotated[list[ID], Field(min_length=1, max_length=10)]
    visible_ms: Annotated[int, Field(ge=1000, le=60000)] = 1000


class Review(BaseModel):
    client_request_id: UUID
    snapshot_id: UUID
    quality_run_id: UUID
    metadata_version_id: UUID
    reviewer_label: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
    decision: Literal["confirm", "reject"]
    comment: Annotated[str, Field(max_length=1000)] | None = None


class Correction(BaseModel):
    client_request_id: UUID
    base_version_id: UUID
    reviewer_label: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
    title: Annotated[str, Field(max_length=10000)]
    text: Annotated[str, Field(max_length=2000000)]
    url: Annotated[str, Field(max_length=10000)]
    lang: Annotated[str, Field(max_length=20)]
    content_type: Annotated[str, Field(max_length=100)] = "HTML"


def error(message, status=503, code="DependencyUnavailable"):
    return JSONResponse(
        status_code=status,
        headers={"Retry-After": "5"} if status == 503 else {},
        content={
            "request_id": str(uuid4()),
            "code": code,
            "message": message,
            "retryable": status == 503,
            "issues": [],
        },
    )


def unavailable(message):
    return error(message)


@app.exception_handler(s.Fault)
def fault_handler(request, exc):
    return error(str(exc), exc.status, exc.code)


@app.exception_handler(RequestValidationError)
def validation_handler(request, exc):
    return error(
        "Check identifiers, required fields, and the result limit (1-10).", 422, "InvalidInput"
    )


@app.exception_handler(Exception)
def exception_handler(request, exc):
    logging.getLogger(__name__).exception("Request failed: %s", request.url.path)
    return error(
        "A required service or model artifact is unavailable. Retry or inspect server logs."
    )


def database_available():
    try:
        with s.connect() as connection:
            return connection.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
    except psycopg.Error:
        return False


@app.get("/health", include_in_schema=False)
def health():
    return (
        {"status": "healthy", "database": "connected"}
        if database_available()
        else unavailable("Required database is unavailable.")
    )


@app.get("/ready")
def ready():
    if not database_available():
        return unavailable("Required database is unavailable.")
    try:
        with s.connect() as connection:
            release, ranker = s.runtime(connection)
            return {"status": "ready", "context": s.context(release, ranker)}
    except (s.Fault, OSError, psycopg.Error):
        return unavailable("Publish a readable model release with cmdl train.")


@app.get("/demo/accounts")
def accounts():
    with s.connect() as connection:
        _, ranker = s.runtime(connection)
        people = sorted(ranker.history, key=lambda p: (-len(ranker.consumed[p]), p))
        return {
            "accounts": [
                {"person_id": p, "historical_articles": len(ranker.consumed[p])} for p in people[:8]
            ],
            "new_account": "0",
        }


@app.get("/recommend/{person_id}")
def recommend(
    person_id: ID,
    limit: Limit = 10,
    session_id: UUID | None = None,
    snapshot_id: UUID | None = None,
    mode: str = "quality_hybrid",
):
    if mode not in MODELS:
        raise s.Fault("Unknown ranking mode.", 422, "InvalidInput")
    with s.connect() as connection:
        return s.recommend(
            connection, person_id, session_id, limit=limit, mode=mode, snapshot_id=snapshot_id
        )


@app.get("/feed")
def anonymous_feed(session_id: UUID, limit: Limit = 10):
    with s.connect() as connection:
        return s.recommend(connection, session=session_id, limit=limit)


@app.get("/similar/{content_id}")
def similar(
    content_id: ID,
    limit: Limit = 10,
    person_id: ID | None = None,
    session_id: UUID | None = None,
    snapshot_id: UUID | None = None,
):
    with s.connect() as connection:
        return s.recommend(
            connection, person_id, session_id, content_id, limit, snapshot_id=snapshot_id
        )


@app.get("/reader/preferences")
def reader_preferences(session_id: UUID, person_id: ID | None = None):
    with s.connect() as connection:
        _, ranker = s.runtime(connection)
        languages = sorted({r["lang"].lower() for r in ranker.catalog.values() if r["lang"]})
        return {
            "language": feedback.preference(connection, person_id, session_id),
            "available_languages": languages,
        }


@app.put("/reader/preferences")
def update_reader_preferences(body: ReaderPreference):
    with s.connect() as connection:
        _, ranker = s.runtime(connection)
        languages = {r["lang"].lower() for r in ranker.catalog.values() if r["lang"]}
        return feedback.save_preference(connection, body.model_dump(mode="json"), languages)


@app.post("/feed/impressions")
def feed_impressions(body: FeedImpressions):
    with s.connect() as connection:
        return feedback.record_impressions(connection, body.model_dump(mode="json"))


@app.get("/articles/{content_id}")
def article(content_id: ID, snapshot_id: UUID | None = None):
    with s.connect() as connection:
        release, ranker = s.runtime(connection, snapshot_id)
        return {
            "context": s.context(release, ranker),
            "article": s.article_record(connection, ranker, content_id),
        }


@app.post("/reading/open")
def open_article(body: OpenArticle):
    with s.connect() as connection:
        article, event, payload = s.begin_read(connection, body.model_dump(mode="json"))
        request_id = s.request_next(connection, payload)
        release, ranker = s.runtime(connection)
        selected_context = s.context(release, ranker)
    s.POOL.submit(s.prepare_next, request_id)
    return {
        "article": article,
        "context": selected_context,
        "event": event,
        "prefetch_id": request_id,
        "navigation_id": str(body.navigation_id),
    }


@app.post("/reading/complete")
def complete(body: Completion):
    with s.connect() as connection:
        return s.complete_read(connection, body.model_dump(mode="json"))


@app.post("/reading/reset")
def reset(body: Reset):
    with s.connect() as connection:
        feedback.invalidate(connection, str(body.session_id), body.person_id)
        connection.execute(
            (
                "UPDATE reader_navigation SET "
                "navigation_id=%s,person_id=%s,updated_at=clock_timestamp()"
                " WHERE session_id=%s"
            ),
            (body.navigation_id, body.person_id, body.session_id),
        )
    return {"status": "reset"}


@app.post("/reading/estimate")
def estimate(body: EstimatedCompletion):
    with s.connect() as connection:
        return s.estimate_read(connection, body.model_dump(mode="json"))


@app.post("/next")
def next_article(body: NextRequest):
    with s.connect() as connection:
        request_id = s.request_next(connection, body.model_dump(mode="json"))
    s.POOL.submit(s.prepare_next, request_id)
    return {"request_id": request_id, "status": "pending"}


@app.get("/next/{request_id}")
def next_status(request_id: UUID, session_id: UUID, navigation_id: UUID):
    with s.connect() as connection:
        return s.next_result(connection, request_id, session_id, navigation_id)


@app.get("/quality/report")
def quality_report(snapshot_id: UUID | None = None):
    with s.connect() as connection:
        s.runtime(connection, snapshot_id)
        return s.collection_report(connection)


@app.get("/quality/queue")
def quality_queue():
    with s.connect() as connection:
        _, ranker = s.runtime(connection)
        rows = connection.execute(
            "SELECT metadata_version_id FROM assessment_warnings "
            "ORDER BY metadata_version_id,warning_id"
        ).fetchall()
        states = s.overlay(connection)
        current = {
            r["metadata_version_id"]: i
            for i in ranker.ids
            if (r := s.article_record(connection, ranker, i, states))["availability"] == "available"
        }
        selected = {str(r["metadata_version_id"]) for r in rows} & current.keys()
        return {
            "articles": [
                {
                    "content_id": current[v],
                    "metadata_version_id": v,
                    "title": s.article_record(connection, ranker, current[v], states)["metadata"][
                        "title"
                    ],
                }
                for v in sorted(selected)
            ]
        }


@app.get("/quality/{record_version_id}")
def quality_record(record_version_id: UUID, snapshot_id: UUID | None = None):
    with s.connect() as connection:
        release, ranker = s.runtime(connection, snapshot_id)
        exists = connection.execute(
            "SELECT content_id FROM metadata_versions WHERE metadata_version_id=%s",
            (record_version_id,),
        ).fetchone()
        if not exists:
            raise s.Fault("Unknown metadata version.", 404, "NotFound")
        member = s.article_record(connection, ranker, exists["content_id"])
        if member["metadata_version_id"] != str(record_version_id):
            raise s.Fault("This version is not the current record in the selected catalog.")
        return {
            "context": s.context(release, ranker),
            "quality": s.quality(connection, record_version_id, release["snapshot_id"]),
        }


@app.post("/quality/warnings/{warning_id}/reviews")
def review(warning_id: UUID, body: Review):
    payload = body.model_dump(mode="json")
    payload["reviewer_label"] = payload["reviewer_label"].strip()
    with s.connect() as connection:
        result, created = s.submit_review(connection, warning_id, payload)
    return JSONResponse(status_code=201 if created else 200, content=result)


@app.post("/reviewer/articles/{content_id}/versions", status_code=201)
def correction(content_id: ID, body: Correction):
    with s.connect() as connection:
        return s.correct_metadata(connection, content_id, body.model_dump(mode="json"))
