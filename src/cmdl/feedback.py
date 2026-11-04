"""Persistent live preferences, delivered feeds, and browser visibility receipts."""

from datetime import datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.types.json import Jsonb

from cmdl.recommender.feedback_policy import VERSION, exposure_strength, language_strength


def subject(person, session):
    return "user:" + person if person is not None else "session:" + str(session)


def invalidate(connection, session, person):
    connection.execute(
        "INSERT INTO reader_feed_state VALUES (%s,%s,NULL,DEFAULT) "
        "ON CONFLICT(session_id) DO UPDATE SET person_id=excluded.person_id,"
        "feed_id=NULL,updated_at=clock_timestamp()",
        (session, person),
    )


def preference(connection, person, session):
    row = connection.execute(
        "SELECT language FROM reader_preference_events WHERE subject=%s "
        "ORDER BY created_at DESC,client_request_id DESC LIMIT 1",
        (subject(person, session),),
    ).fetchone()
    return row["language"] if row else "auto"


def save_preference(connection, payload, available):
    from cmdl.serving import Fault

    mode = payload["language"]
    if mode not in {"auto", "all", *available}:
        raise Fault("Choose an available article language, auto, or all.", 422, "InvalidInput")
    identity = subject(payload["person_id"], payload["session_id"])
    connection.execute(
        "INSERT INTO reader_preference_events(client_request_id,subject,language) "
        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
        (payload["client_request_id"], identity, mode),
    )
    saved = connection.execute(
        "SELECT * FROM reader_preference_events WHERE client_request_id=%s",
        (payload["client_request_id"],),
    ).fetchone()
    if saved["subject"] != identity or saved["language"] != mode:
        raise Fault("This retry key belongs to another preference.")
    return {"language": mode, "client_request_id": str(saved["client_request_id"])}


def policy(connection, person, session, at):
    selector, value = (
        ("person_id=%s", person)
        if person is not None
        else (
            "person_id IS NULL AND session_id=%s",
            session,
        )
    )
    mode = preference(connection, person, session)
    reads = (
        connection.execute(
            "SELECT m.lang,r.weight,EXTRACT(EPOCH FROM (%s::timestamptz-r.last_at))::float8 "
            "AS age_seconds FROM (SELECT DISTINCT ON(content_id) content_id,metadata_version_id,"
            "MAX(CASE kind WHEN 'complete' THEN 3 WHEN 'estimated_complete' THEN 2 ELSE 1 END) "
            "OVER (PARTITION BY content_id) AS weight,"
            "MAX(created_at) OVER (PARTITION BY content_id) AS last_at FROM live_reading_events "
            "WHERE " + selector + " AND created_at <= %s::timestamptz "
            "AND created_at > %s::timestamptz - INTERVAL '30 days' "
            "ORDER BY content_id,created_at DESC) r JOIN metadata_versions m "
            "USING(metadata_version_id) "
            "ORDER BY r.last_at DESC LIMIT 20",
            (at, value, at, at),
        ).fetchall()
        if session is not None or person is not None
        else []
    )
    impressions = (
        connection.execute(
            "SELECT i.content_id,EXTRACT(EPOCH FROM (%s::timestamptz-i.created_at))::float8 "
            "AS age_seconds FROM feed_impressions i JOIN feed_deliveries d USING(feed_id) WHERE d."
            + selector
            + " AND i.created_at <= %s::timestamptz "
            "AND i.created_at > %s::timestamptz - INTERVAL '7 days'",
            (at, value, at, at),
        ).fetchall()
        if session is not None or person is not None
        else []
    )
    languages = language_strength(reads)
    exposures = exposure_strength(impressions)
    return {
        "version": VERSION,
        "language": mode,
        "learned_languages": languages,
        "exposures": exposures,
        "active": mode not in ("auto", "all")
        or mode == "auto"
        and bool(languages)
        or any(v > 1 for v in exposures.values()),
    }


def deliver(connection, person, session, release_id, items):
    feed_id = uuid4()
    connection.execute(
        "INSERT INTO feed_deliveries(feed_id,session_id,person_id,release_id,items) "
        "VALUES (%s,%s,%s,%s,%s)",
        (
            feed_id,
            session,
            person,
            release_id,
            Jsonb({item["content_id"]: item["article"]["metadata_version_id"] for item in items}),
        ),
    )
    connection.execute(
        "INSERT INTO reader_feed_state VALUES (%s,%s,%s,DEFAULT) "
        "ON CONFLICT(session_id) DO UPDATE SET person_id=excluded.person_id,"
        "feed_id=excluded.feed_id,updated_at=clock_timestamp()",
        (session, person, feed_id),
    )
    return str(feed_id)


def record_impressions(connection, payload):
    from cmdl import serving as s

    feed = connection.execute(
        "SELECT * FROM feed_deliveries WHERE feed_id=%s",
        (payload["feed_id"],),
    ).fetchone()
    if (
        not feed
        or str(feed["session_id"]) != payload["session_id"]
        or feed["person_id"] != payload["person_id"]
    ):
        raise s.Fault("This feed belongs to another reader.")
    ids = sorted(set(payload["content_ids"]))
    if any(i not in feed["items"] for i in ids):
        raise s.Fault("Only delivered articles can count as impressions.", 422, "InvalidInput")
    active = connection.execute(
        "SELECT * FROM reader_feed_state WHERE session_id=%s FOR UPDATE",
        (payload["session_id"],),
    ).fetchone()
    saved = {
        r["content_id"]
        for r in connection.execute(
            "SELECT content_id FROM feed_impressions WHERE feed_id=%s",
            (payload["feed_id"],),
        )
    }
    pending = [i for i in ids if i not in saved]
    if pending:
        if (
            not active
            or str(active["feed_id"]) != payload["feed_id"]
            or active["person_id"] != payload["person_id"]
        ):
            raise s.Fault("This feed was replaced.", 409, "StaleFeed")
        if (datetime.fromisoformat(s.now()) - feed["created_at"]).total_seconds() > 86400:
            raise s.Fault("Refresh this expired feed.", 409, "StaleFeed")
        _, ranker = s.runtime(connection)
        states = s.overlay(connection)
        for item in pending:
            record = s.article_record(connection, ranker, item, states)
            if (
                record["availability"] != "available"
                or record["metadata_version_id"] != feed["items"][item]
            ):
                raise s.Fault("This article version is no longer available.")
            connection.execute(
                "INSERT INTO feed_impressions VALUES (%s,%s,%s,%s,%s,%s,DEFAULT) "
                "ON CONFLICT DO NOTHING",
                (
                    uuid5(NAMESPACE_URL, f"{payload['feed_id']}:{item}"),
                    payload["feed_id"],
                    item,
                    feed["items"][item],
                    payload["visible_ms"],
                    "feed-visibility-v1",
                ),
            )
    return {"feed_id": payload["feed_id"], "recorded": ids}
