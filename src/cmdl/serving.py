"Live overlay and persistent operations; historical model/evaluation inputs are immutable."

import hashlib
import json
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cmdl import feedback
from cmdl.quality.checks import DIMENSIONS, VERSION
from cmdl.quality.store import persist
from cmdl.reading import ESTIMATE_RULE, required_seconds
from cmdl.recommender.feedback_policy import adjust

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cmdl-next")
LOCK = threading.RLock()
LOADED = {}


class Fault(ValueError):
    def __init__(self, message, status=409, code="Conflict"):
        super().__init__(message)
        self.status, self.code = status, code


def now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def connect():
    return psycopg.connect(connect_timeout=3, row_factory=dict_row)


def runtime(connection, snapshot_id=None):
    release = connection.execute(
        "SELECT r.* FROM serving_releases r JOIN app_settings s "
        "ON r.release_id::text=s.value WHERE "
        "s.key='active_release'"
    ).fetchone()
    if not release:
        raise Fault(
            "Run cmdl ingest and cmdl train to publish recommendation artifacts.",
            503,
            "DependencyUnavailable",
        )
    if snapshot_id and str(release["snapshot_id"]) != str(snapshot_id):
        raise Fault("No serving release is active for the requested snapshot.")
    key = str(release["release_id"])
    with LOCK:
        if key not in LOADED:
            data = Path(release["artifact_path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != release["artifact_sha256"]:
                raise Fault(
                    "The serving artifact failed its checksum.", 503, "DependencyUnavailable"
                )
            # Only locally published, checksum-verified artifacts may be loaded.
            LOADED.clear()
            LOADED[key] = pickle.loads(data)
        return release, LOADED[key]


def context(release, ranker):
    return {
        "snapshot_id": str(release["snapshot_id"]),
        "as_of_at": ranker.cutoff,
        "request_time": now(),
        "model_run_id": str(release["release_id"]),
        "protocol_id": "cmdl-live-v2",
        "metadata_variant_id": "original-with-live-corrections",
    }


def overlay(connection):
    events = connection.execute(
        "SELECT DISTINCT ON(content_id) content_id,kind FROM "
        "live_catalog_events WHERE effective_at <= "
        "clock_timestamp() ORDER BY content_id,effective_at "
        "DESC,event_id DESC"
    ).fetchall()
    heads = connection.execute("SELECT * FROM live_metadata_heads").fetchall()
    return {r["content_id"] for r in events if r["kind"] == "removed"}, {
        r["content_id"]: str(r["metadata_version_id"]) for r in heads
    }


def recent_reads(connection, person, session):
    if person is not None:
        rows = connection.execute(
            (
                "SELECT content_id FROM live_reading_events WHERE "
                "person_id=%s ORDER BY created_at,client_request_id"
            ),
            (person,),
        )
    elif session is not None:
        rows = connection.execute(
            (
                "SELECT content_id FROM live_reading_events WHERE "
                "session_id=%s AND person_id IS NULL ORDER BY "
                "created_at,client_request_id"
            ),
            (session,),
        )
    else:
        return []
    return [r["content_id"] for r in rows]


def article_record(connection, ranker, content_id, states=None):
    if content_id not in ranker.catalog:
        known = connection.execute(
            "SELECT 1 FROM articles WHERE content_id=%s", (content_id,)
        ).fetchone()
        if known:
            return {
                "content_id": content_id,
                "availability": "unavailable",
                "metadata": None,
                "metadata_version_id": None,
            }
        raise Fault("Unknown article.", 404, "NotFound")
    removed, heads = states if states is not None else overlay(connection)
    if content_id in removed:
        return {
            "content_id": content_id,
            "availability": "removed",
            "metadata": None,
            "metadata_version_id": None,
        }
    record = ranker.catalog[content_id]
    if content_id in heads:
        record = connection.execute(
            "SELECT * FROM metadata_versions WHERE metadata_version_id=%s", (heads[content_id],)
        ).fetchone()
    metadata = {k: record[k] for k in ("title", "text", "url", "lang", "content_type")}
    return {
        "content_id": content_id,
        "availability": "available",
        "metadata_version_id": str(record["metadata_version_id"]),
        "metadata": metadata,
    }


def quality(connection, version, snapshot_id):
    row = connection.execute(
        (
            "SELECT result FROM metadata_assessments WHERE "
            "metadata_version_id=%s AND checks_version=%s"
        ),
        (version, VERSION),
    ).fetchone()
    if not row:
        return {
            "status": "unavailable",
            "reason": "not_run",
            "metadata_version_id": str(version),
            "snapshot_id": str(snapshot_id),
        }
    result = {**row["result"], "snapshot_id": str(snapshot_id)}
    reviews = connection.execute(
        (
            "SELECT r.* FROM assessment_reviews r JOIN "
            "assessment_warnings w USING(warning_id) WHERE "
            "w.metadata_version_id=%s ORDER BY created_at,review_id"
        ),
        (version,),
    ).fetchall()
    result["warnings"] = [
        {
            **w,
            "reviews": [review_json(r) for r in reviews if str(r["warning_id"]) == w["warning_id"]],
        }
        for w in result["warnings"]
    ]
    return result


def recommend(
    connection,
    person=None,
    session=None,
    current=None,
    limit=10,
    mode="quality_hybrid",
    snapshot_id=None,
):
    release, ranker = runtime(connection, snapshot_id)
    removed, heads = overlay(connection)
    recent = recent_reads(connection, person, session)
    preferences = feedback.policy(connection, person, session, now())
    # Never join old features to a newly corrected version. Retraining restores it to ranking.
    outdated = {
        i
        for i, v in heads.items()
        if i in ranker.catalog and str(ranker.catalog[i]["metadata_version_id"]) != v
    }
    banned = removed | outdated | set(recent) | ranker.consumed.get(person, set())
    if current:
        reference = article_record(connection, ranker, current)
        if reference["availability"] != "available":
            raise Fault("The current article is unavailable.")
        banned.add(current)
    if (
        not current
        and not recent
        and mode == "quality_hybrid"
        and person in ranker.cache
        and not preferences["active"]
    ):
        ranked = [r for r in ranker.cache[person] if r["content_id"] not in banned]
    else:
        ranked = []
    if len(ranked) < limit:
        ranked = ranker.rank(
            person,
            current=None if current in outdated else current,
            recent=[i for i in recent if i not in outdated],
            excluded=banned,
            mode=mode,
            limit=len(ranker.ids) if preferences["active"] else limit,
        )
    if preferences["active"]:
        ranked = adjust(
            ranked,
            ranker.catalog,
            preferences["language"],
            preferences["learned_languages"],
            preferences["exposures"],
        )
    items = []
    for item in ranked[:limit]:
        article = article_record(connection, ranker, item["content_id"], (removed, heads))
        if article["availability"] != "available":
            continue
        items.append(
            {
                **item,
                "rank": len(items) + 1,
                "article": article,
                "model_run_id": str(release["release_id"]),
                "feature_set_id": str(release["release_id"]),
                "quality": quality(
                    connection, article["metadata_version_id"], release["snapshot_id"]
                ),
            }
        )
    reasons = []
    if any(i["source"] == "popularity" for i in items):
        reasons.append("insufficient_personalized_signal")
    if not items:
        reasons.append("no_eligible_candidates")
    response = {
        "context": context(release, ranker),
        "person_id": person,
        "reference_content_id": current,
        "requested_limit": limit,
        "returned_count": len(items),
        "short_result": len(items) < limit,
        "fallback_reasons": reasons,
        "items": items,
        "personalization": {k: v for k, v in preferences.items() if k != "exposures"},
    }
    if session is not None and current is None:
        response["feed_id"] = feedback.deliver(
            connection, person, session, release["release_id"], items
        )
    return response


def navigation_matches(connection, payload):
    row = connection.execute(
        "SELECT * FROM reader_navigation WHERE session_id=%s", (payload["session_id"],)
    ).fetchone()
    return bool(
        row
        and all(
            (str(row[k]) if row[k] is not None else None)
            == (str(payload[k]) if payload[k] is not None else None)
            for k in ("person_id", "content_id", "navigation_id")
        )
    )


def event(connection, payload, kind):
    keys = ("session_id", "person_id", "content_id", "metadata_version_id", "navigation_id")
    connection.execute(
        (
            "INSERT INTO live_reading_events VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,DEFAULT) ON CONFLICT DO NOTHING"
        ),
        tuple(payload[k] for k in ("client_request_id", *keys)) + (kind,),
    )
    saved = connection.execute(
        "SELECT * FROM live_reading_events WHERE client_request_id=%s",
        (payload["client_request_id"],),
    ).fetchone()
    if (
        not saved
        or saved["kind"] != kind
        or any(
            (str(saved[k]) if saved[k] is not None else None)
            != (str(payload[k]) if payload[k] is not None else None)
            for k in keys
        )
    ):
        raise Fault("This retry key belongs to a different reading event.")
    return {
        **{k: str(v) if isinstance(v, (UUID, datetime)) else v for k, v in saved.items()},
        "confirmed_read": kind == "complete",
        "estimated_read": kind == "estimated_complete",
    }


def begin_read(connection, payload):
    _, ranker = runtime(connection)
    article = article_record(connection, ranker, payload["content_id"])
    if article["availability"] != "available":
        raise Fault("This article is no longer available.")
    payload = {**payload, "metadata_version_id": article["metadata_version_id"]}
    existing = connection.execute(
        "SELECT * FROM live_reading_events WHERE client_request_id=%s",
        (payload["client_request_id"],),
    ).fetchone()
    if existing:
        saved = event(connection, payload, "view")
        if not navigation_matches(connection, payload):
            raise Fault("This navigation was replaced. Return to the feed.", 409, "StaleNavigation")
    else:
        connection.execute(
            (
                "INSERT INTO reader_navigation VALUES "
                "(%s,%s,%s,%s,DEFAULT) ON CONFLICT(session_id) DO UPDATE"
                " SET "
                "person_id=excluded.person_id,content_id=excluded.content_id,navigation_id=excluded.navigation_id,updated_at=clock_timestamp()"
            ),
            tuple(payload[k] for k in ("session_id", "person_id", "content_id", "navigation_id")),
        )
        saved = event(connection, payload, "view")
    feedback.invalidate(connection, payload["session_id"], payload["person_id"])
    return article, saved, payload


def complete_read(connection, payload):
    if connection.execute(
        "SELECT 1 FROM live_reading_events WHERE client_request_id=%s",
        (payload["client_request_id"],),
    ).fetchone():
        return event(connection, payload, "complete")
    if not navigation_matches(connection, payload):
        raise Fault("This navigation was replaced.", 409, "StaleNavigation")
    opened = connection.execute(
        (
            "SELECT metadata_version_id FROM live_reading_events "
            "WHERE session_id=%s AND navigation_id=%s AND "
            "content_id=%s AND kind='view'"
        ),
        (payload["session_id"], payload["navigation_id"], payload["content_id"]),
    ).fetchone()
    if not opened or str(opened["metadata_version_id"]) != str(payload["metadata_version_id"]):
        raise Fault("Completion must refer to the opened article version.")
    return event(connection, payload, "complete")


def estimate_read(connection, payload):
    # Serialize against account/article switches and duplicate submissions.
    connection.execute(
        "SELECT 1 FROM reader_navigation WHERE session_id=%s FOR UPDATE",
        (payload["session_id"],),
    ).fetchone()
    existing = connection.execute(
        "SELECT * FROM reading_completion_estimates WHERE client_request_id=%s",
        (payload["client_request_id"],),
    ).fetchone()
    if existing:
        if any(
            existing[k] != payload[k] for k in ("rule_version", "active_seconds", "end_visible")
        ):
            raise Fault("This retry key belongs to different reading evidence.")
        return event(connection, payload, "estimated_complete")
    if not navigation_matches(connection, payload):
        raise Fault("This navigation was replaced.", 409, "StaleNavigation")
    opened = connection.execute(
        "SELECT e.metadata_version_id,m.text,e.created_at "
        "FROM live_reading_events e JOIN metadata_versions m USING(metadata_version_id) "
        "WHERE e.session_id=%s AND e.navigation_id=%s AND e.content_id=%s AND e.kind='view'",
        (payload["session_id"], payload["navigation_id"], payload["content_id"]),
    ).fetchone()
    if not opened or str(opened["metadata_version_id"]) != str(payload["metadata_version_id"]):
        raise Fault("Reading estimate must refer to the opened article version.")
    threshold = required_seconds(opened["text"])
    elapsed = (datetime.fromisoformat(now()) - opened["created_at"]).total_seconds()
    if (
        payload["rule_version"] != ESTIMATE_RULE
        or not payload["end_visible"]
        or payload["active_seconds"] < threshold
        or payload["active_seconds"] > elapsed + 2
    ):
        raise Fault(
            "Reading estimate requires active reading time and the visible article end.",
            422,
            "InsufficientReadingEvidence",
        )
    saved = event(connection, payload, "estimated_complete")
    connection.execute(
        "INSERT INTO reading_completion_estimates "
        "(client_request_id,rule_version,active_seconds,required_seconds,end_visible) "
        "VALUES (%s,%s,%s,%s,%s)",
        (payload["client_request_id"], ESTIMATE_RULE, payload["active_seconds"], threshold, True),
    )
    return saved


def request_next(connection, payload):
    if not navigation_matches(connection, payload):
        raise Fault("This navigation was replaced.", 409, "StaleNavigation")
    request_id = payload["prefetch_id"]
    connection.execute(
        (
            "INSERT INTO next_requests VALUES "
            "(%s,%s,%s,%s,%s,'pending',NULL,NULL,DEFAULT) ON "
            "CONFLICT DO NOTHING"
        ),
        (
            request_id,
            *(payload[k] for k in ("session_id", "person_id", "content_id", "navigation_id")),
        ),
    )
    row = connection.execute(
        "SELECT * FROM next_requests WHERE request_id=%s", (request_id,)
    ).fetchone()
    if any(
        (str(row[k]) if row[k] is not None else None)
        != (str(payload[k]) if payload[k] is not None else None)
        for k in ("session_id", "person_id", "content_id", "navigation_id")
    ):
        raise Fault("This prefetch key belongs to another navigation.")
    return str(request_id)


def prepare_next(request_id):
    try:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM next_requests WHERE request_id=%s", (request_id,)
            ).fetchone()
            if not row or not navigation_matches(connection, row):
                return
            result = recommend(
                connection, row["person_id"], row["session_id"], row["content_id"], 1
            )
            connection.execute(
                "UPDATE next_requests SET status='ready',result=%s,error=NULL WHERE request_id=%s",
                (Jsonb(result), request_id),
            )
    except Exception:
        with connect() as connection:
            connection.execute(
                (
                    "UPDATE next_requests SET "
                    "status='failed',error='Recommendation preparation "
                    "failed. Retry.' WHERE request_id=%s"
                ),
                (request_id,),
            )


def next_result(connection, request_id, session_id, navigation_id):
    row = connection.execute(
        "SELECT * FROM next_requests WHERE request_id=%s", (request_id,)
    ).fetchone()
    if not row:
        raise Fault("Unknown prefetch request.", 404, "NotFound")
    if (
        str(row["session_id"]) != str(session_id)
        or str(row["navigation_id"]) != str(navigation_id)
        or not navigation_matches(connection, row)
    ):
        raise Fault("This recommendation belongs to an older navigation.", 409, "StaleNavigation")
    result = row["result"]
    if row["status"] == "ready":
        # Recheck removals, corrections, recent reads and the selected model release.
        result = recommend(connection, row["person_id"], row["session_id"], row["content_id"], 1)
    return {
        "request_id": str(request_id),
        "navigation_id": str(navigation_id),
        "content_id": row["content_id"],
        "person_id": row["person_id"],
        "status": row["status"],
        "result": result,
        "error": row["error"],
    }


def review_json(row):
    return {
        **row["payload"],
        "warning_id": str(row["warning_id"]),
        "review_id": str(row["review_id"]),
        "created_at": row["created_at"].isoformat().replace("+00:00", "Z"),
    }


def submit_review(connection, warning_id, payload):
    warning = connection.execute(
        "SELECT * FROM assessment_warnings WHERE warning_id=%s", (warning_id,)
    ).fetchone()
    if not warning:
        raise Fault("Unknown warning.", 404, "NotFound")
    if str(warning["metadata_version_id"]) != str(payload["metadata_version_id"]) or str(
        warning["assessment_id"]
    ) != str(payload["quality_run_id"]):
        raise Fault("Warning, assessment, and metadata version must match.")
    release, ranker = runtime(connection, payload["snapshot_id"])
    version = str(payload["metadata_version_id"])
    if (
        version not in {str(r["metadata_version_id"]) for r in ranker.catalog.values()}
        and not connection.execute(
            "SELECT 1 FROM live_metadata_heads WHERE metadata_version_id=%s", (version,)
        ).fetchone()
    ):
        raise Fault("The warning version is outside this catalog.")
    payload = json.loads(json.dumps(payload, default=str))
    inserted = connection.execute(
        (
            "INSERT INTO "
            "assessment_reviews(warning_id,client_request_id,payload)"
            " VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING "
            "review_id"
        ),
        (warning_id, payload["client_request_id"], Jsonb(payload)),
    ).fetchone()
    saved = connection.execute(
        "SELECT * FROM assessment_reviews WHERE client_request_id=%s",
        (payload["client_request_id"],),
    ).fetchone()
    if str(saved["warning_id"]) != str(warning_id) or saved["payload"] != payload:
        raise Fault("This retry key was used for a different review.")
    return review_json(saved), bool(inserted)


def collection_report(connection):
    release, ranker = runtime(connection)
    states = overlay(connection)
    records = [article_record(connection, ranker, i, states) for i in ranker.ids]
    records = [r for r in records if r["availability"] == "available"]
    versions = [r["metadata_version_id"] for r in records]
    results = [
        r["result"]
        for r in connection.execute(
            (
                "SELECT result FROM metadata_assessments WHERE "
                "metadata_version_id=ANY(%s::uuid[]) AND "
                "checks_version=%s"
            ),
            (versions, VERSION),
        )
    ]
    counts = connection.execute(
        (
            "SELECT r.payload->>'decision' decision,count(*) count "
            "FROM assessment_reviews r JOIN assessment_warnings w "
            "USING(warning_id) WHERE "
            "w.metadata_version_id=ANY(%s::uuid[]) GROUP BY 1"
        ),
        (versions,),
    ).fetchall()
    return {
        "context": context(release, ranker),
        "status": "available" if results else "unavailable",
        "reason": "not_run" if not results else None,
        "catalog_records": len(records),
        "assessed_records": len(results),
        "unassessed_records": len(records) - len(results),
        "warning_count": sum(len(r["warnings"]) for r in results),
        "review_decision_counts": {
            d: next((r["count"] for r in counts if r["decision"] == d), 0)
            for d in ("confirm", "reject")
        },
        "dimension_means": {
            d: sum(r["dimensions"][d]["score"] or 0 for r in results) / len(results)
            if results
            else None
            for d in DIMENSIONS
        },
        "dimension_denominators": {
            d: sum(r["dimensions"][d]["score"] is not None for r in results) for d in DIMENSIONS
        },
    }


def correct_metadata(connection, content_id, payload):
    existing = connection.execute(
        "SELECT * FROM metadata_versions WHERE metadata_version_id=%s",
        (payload["client_request_id"],),
    ).fetchone()
    if existing:
        keys = ("title", "text", "url", "lang", "content_type")
        if (
            existing["content_id"] != content_id
            or str(existing["base_version_id"]) != str(payload["base_version_id"])
            or any(existing[k] != payload[k] for k in keys)
        ):
            raise Fault("This correction retry key was used for a different version.")
        return {
            "metadata_version_id": str(existing["metadata_version_id"]),
            "base_version_id": str(existing["base_version_id"]),
            "message": "This corrected version was already saved and checked.",
        }
    _, ranker = runtime(connection)
    current = article_record(connection, ranker, content_id)
    if current["metadata_version_id"] != str(payload["base_version_id"]):
        raise Fault("The article version changed. Reload before correcting it.")
    base = connection.execute(
        "SELECT * FROM metadata_versions WHERE metadata_version_id=%s",
        (payload["base_version_id"],),
    ).fetchone()
    version = payload["client_request_id"]
    variant = "correction-" + str(version)
    metadata = {k: payload[k] for k in ("title", "text", "url", "lang", "content_type")}
    checksum = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    config = {
        "editor": payload["reviewer_label"],
        "base_version_id": str(payload["base_version_id"]),
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    connection.execute(
        "INSERT INTO metadata_variants VALUES (%s,'correction',%s,%s,NULL,DEFAULT)",
        (variant, Jsonb(config), config_hash),
    )
    connection.execute(
        (
            "INSERT INTO "
            "metadata_versions(metadata_version_id,content_id,share_event_id,variant_id,base_version_id,title,text,url,lang,content_type,payload_sha256)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        ),
        (
            version,
            content_id,
            base["share_event_id"],
            variant,
            base["metadata_version_id"],
            *metadata.values(),
            checksum,
        ),
    )
    result = persist(connection, {**metadata, "metadata_version_id": str(version)})
    connection.execute(
        (
            "INSERT INTO live_metadata_heads VALUES (%s,%s,DEFAULT) "
            "ON CONFLICT(content_id) DO UPDATE SET "
            "metadata_version_id=excluded.metadata_version_id,updated_at=clock_timestamp()"
        ),
        (content_id, version),
    )
    return {
        "metadata_version_id": str(version),
        "base_version_id": str(payload["base_version_id"]),
        "quality": result,
        "message": (
            "New version checked. Run cmdl train to refresh content "
            "features; the old version is excluded meanwhile."
        ),
    }
