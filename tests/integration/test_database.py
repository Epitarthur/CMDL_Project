"""Real PostgreSQL storage checks. Set CMDL_TEST_DSN to a disposable test database.

Each test uses an isolated schema, never production tables. CI always sets the DSN;
without it these tests skip so source/contract tests still work without Docker.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from cmdl.db import migrate as migration

A = "a" * 64
B = "b" * 64
CUTOFF = datetime(2017, 1, 1, tzinfo=UTC)


@pytest.fixture
def empty_db():
    dsn = os.environ.get("CMDL_TEST_DSN")
    if not dsn:
        pytest.skip("Set CMDL_TEST_DSN for real PostgreSQL migration/constraint checks")
    schema = "t06_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    with psycopg.connect(dsn, options=f"-c search_path={schema} -c timezone=UTC") as connection:
        try:
            yield connection
        finally:
            connection.rollback()
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def put(db, table, **values):
    fields = list(values)
    return db.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, fields)),
            sql.SQL(",").join(sql.Placeholder() for _ in fields),
        ),
        [Jsonb(v) if isinstance(v, dict | list) else v for v in values.values()],
    ).fetchone()


def metadata(db, event, content="10", variant="original", base=None):
    return put(
        db,
        "metadata_versions",
        content_id=content,
        share_event_id=event,
        variant_id=variant,
        base_version_id=base,
        title="Original title",
        text="Original text",
        url="https://example.org/article",
        lang="en",
        content_type="HTML",
        payload_sha256=A,
    )[0]


def seed(db, publish=True):
    for digest, category in ((A, "article"), (B, "interaction")):
        put(
            db,
            "source_files",
            sha256=digest,
            filename=category + ".csv",
            bytes=10,
            dataset_ref="deskdrop",
            upstream_version="5",
            event_category=category,
        )
    put(db, "users", person_id="-9223372036854775808")
    put(db, "metadata_variants", variant_id="original", kind="original", config={}, config_sha256=A)
    events, versions = [], []
    for n, content in enumerate(("10", "2"), 1):
        put(db, "articles", content_id=content)
        put(db, "source_rows", file_sha256=A, row_number=n, raw_fields={"contentId": content})
        event = put(
            db,
            "article_events",
            content_id=content,
            event_type="CONTENT SHARED",
            event_at="2016-12-01T00:00:00Z",
            file_sha256=A,
            row_number=n,
        )[0]
        events.append(event)
        versions.append(metadata(db, event, content))
    raw = {"personId": "-9223372036854775808", "contentId": "10", "sessionId": "9007199254740993"}
    interactions = []
    for n in (1, 2):
        put(
            db,
            "source_rows",
            file_sha256=B,
            row_number=n,
            raw_fields=raw,
            duplicate_file_sha256=B if n == 2 else None,
            duplicate_row_number=1 if n == 2 else None,
        )
        interactions.append(
            put(
                db,
                "interactions",
                person_id=raw["personId"],
                content_id="10",
                session_id=raw["sessionId"],
                event_type="VIEW",
                event_at="2016-12-02T02:30:00+02:30",
                file_sha256=B,
                row_number=n,
            )[0]
        )
    snapshot = put(
        db,
        "snapshots",
        protocol_id="deskdrop-temporal-v1",
        as_of_at=CUTOFF,
        source_manifest={"files": [A, B]},
        transform_version="test-v1",
        metadata_variant_id="original",
    )[0]
    for content, version in zip(("10", "2"), versions, strict=True):
        put(
            db,
            "snapshot_articles",
            snapshot_id=snapshot,
            content_id=content,
            metadata_version_id=version,
            availability="available",
        )
    for n, interaction in enumerate(interactions):
        put(
            db,
            "snapshot_interactions",
            snapshot_id=snapshot,
            interaction_id=interaction,
            duplicate_of_interaction_id=interactions[0] if n else None,
        )
    if publish:
        db.execute("UPDATE snapshots SET status='published' WHERE snapshot_id=%s", (snapshot,))
    return {
        "snapshot": snapshot,
        "events": events,
        "versions": versions,
        "interactions": interactions,
    }


@pytest.fixture
def db(empty_db):
    migration.migrate(empty_db)
    yield empty_db


@pytest.fixture
def seeded(db):
    return db, seed(db)


def fails(db, query, params=(), error=psycopg.IntegrityError):
    with pytest.raises(error), db.transaction():
        db.execute(query, params)


def quality(db, s, complete=True):
    run = put(
        db,
        "quality_runs",
        snapshot_id=s["snapshot"],
        checks_version="checks-v1",
        config={},
        config_sha256=A,
    )[0]
    db.execute("UPDATE quality_runs SET status='running' WHERE quality_run_id=%s", (run,))
    coverage = {
        d: {"assessed_checks": 1, "passed_checks": 1, "unassessed_checks": 0}
        for d in ("completeness", "validity", "consistency", "informativeness")
    }
    for version in s["versions"]:
        put(
            db,
            "quality_results",
            quality_run_id=run,
            metadata_version_id=version,
            snapshot_id=s["snapshot"],
            completeness_score=1,
            validity_score=1,
            consistency_score=1,
            informativeness_score=1,
            overall_score=1,
            dimension_coverage=coverage,
        )
    warning = put(
        db,
        "quality_warnings",
        quality_run_id=run,
        metadata_version_id=s["versions"][0],
        check_id="test",
        field="title",
        dimension="informativeness",
        severity="warning",
        reason="Test signal",
    )[0]
    if complete:
        db.execute(
            "UPDATE quality_runs SET status='completed', "
            "completed_at=clock_timestamp() WHERE quality_run_id=%s",
            (run,),
        )
    return run, warning


def model(db, s, complete=True, **overrides):
    params = dict(
        snapshot_id=s["snapshot"],
        protocol_id="deskdrop-temporal-v1",
        model_type="popularity",
        manifest={},
    )
    params.update(overrides)
    run = put(db, "model_runs", **params)[0]
    db.execute("UPDATE model_runs SET status='running' WHERE model_run_id=%s", (run,))
    if complete:
        db.execute("UPDATE model_runs SET status='completed' WHERE model_run_id=%s", (run,))
    return run


def ranking(db, s, **overrides):
    params = dict(snapshot_id=s["snapshot"], model_run_id=model(db, s), kind="user")
    params.update(overrides)
    return put(db, "ranking_sets", **params)[0]


def entry(db, s, rankings, content="10", rank=1, score=1, **overrides):
    params = dict(
        ranking_set_id=rankings,
        snapshot_id=s["snapshot"],
        person_id="-9223372036854775808",
        content_id=content,
        metadata_version_id=s["versions"][("10", "2").index(content)],
        rank=rank,
        score=score,
        explanation_code="test",
    )
    params.update(overrides)
    return put(db, "prepared_rankings", **params)[0]


def test_empty_migration_and_repeat_are_atomic(empty_db):
    assert migration.migrate(empty_db) == ["0001_storage_v1.sql"]
    assert migration.migrate(empty_db) == []
    assert (
        empty_db.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema()"
        ).fetchone()[0]
        == 21
    )
    assert empty_db.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)


def test_failed_migration_rolls_back_ddl_and_history(db, tmp_path, monkeypatch):
    folder = tmp_path / "migrations"
    folder.mkdir()
    source = Path("src/cmdl/db/migrations/0001_storage_v1.sql").read_bytes()
    (folder / "0001_storage_v1.sql").write_bytes(source)
    (folder / "0002_broken.sql").write_text("CREATE TABLE rollback_probe(id int); SELECT 1/0;")
    monkeypatch.setattr(migration, "files", lambda _: tmp_path)
    with pytest.raises(psycopg.errors.DivisionByZero):
        migration.migrate(db)
    assert db.execute("SELECT to_regclass('rollback_probe')").fetchone() == (None,)
    assert db.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)


def test_checksum_mismatch_is_rejected(db):
    db.execute("UPDATE schema_migrations SET sha256=%s", (B,))
    db.commit()
    with pytest.raises(migration.MigrationError, match="checksum"):
        migration.migrate(db)


def test_unknown_history_is_rejected(db):
    db.execute("INSERT INTO schema_migrations VALUES ('9999_unknown.sql', %s, now())", (A,))
    db.commit()
    with pytest.raises(migration.MigrationError, match="history"):
        migration.migrate(db)


def test_concurrent_migrators_are_serialized(empty_db):
    options = empty_db.info.options
    dsn = os.environ["CMDL_TEST_DSN"]

    def apply():
        with psycopg.connect(dsn, options=options) as other:
            return migration.migrate(other)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply(), range(2)))
    assert sorted(map(len, results)) == [0, 1]


def test_ids_utc_duplicates_and_original_strings_survive(seeded):
    db, s = seeded
    assert db.execute(
        "SELECT person_id, session_id, event_at FROM interactions LIMIT 1"
    ).fetchone() == ("-9223372036854775808", "9007199254740993", datetime(2016, 12, 2, tzinfo=UTC))
    assert db.execute("SELECT count(*) FROM interactions").fetchone() == (2,)
    assert db.execute(
        "SELECT count(*) FROM snapshot_interactions WHERE duplicate_of_interaction_id IS NOT NULL"
    ).fetchone() == (1,)
    assert db.execute(
        "SELECT title FROM metadata_versions WHERE metadata_version_id=%s", (s["versions"][0],)
    ).fetchone() == ("Original title",)
    assert db.execute(
        "SELECT i.event_at < f.imported_at FROM interactions i JOIN "
        "source_files f ON i.file_sha256=f.sha256 LIMIT 1"
    ).fetchone() == (True,)


@pytest.mark.parametrize("value", ["1.0", "01", "123\n", "1e18", "+2", "", "NaN"])
def test_malformed_source_ids_rejected(db, value):
    fails(db, "INSERT INTO users(person_id) VALUES (%s)", (value,))


def test_unknown_references_rejected(db):
    fails(
        db,
        "INSERT INTO "
        "article_events(content_id,event_type,event_at,file_sha256,row_number) "
        "VALUES ('99','CONTENT SHARED',now(),%s,1)",
        (A,),
    )
    fails(db, "INSERT INTO source_rows(file_sha256,row_number,raw_fields) VALUES (%s,1,'{}')", (A,))


def test_duplicate_group_requires_earliest_full_row(db):
    put(
        db,
        "source_files",
        sha256=B,
        filename="events",
        bytes=0,
        dataset_ref="test",
        upstream_version="5",
        event_category="interaction",
    )
    put(db, "source_rows", file_sha256=B, row_number=1, raw_fields={"value": "same"})
    fails(db, 'INSERT INTO source_rows VALUES (%s,2,\'{"value":"same"}\',null,null)', (B,))
    put(
        db,
        "source_rows",
        file_sha256=B,
        row_number=2,
        raw_fields={"value": "same"},
        duplicate_file_sha256=B,
        duplicate_row_number=1,
    )
    fails(db, 'INSERT INTO source_rows VALUES (%s,3,\'{"value":"same"}\',%s,2)', (B, B))
    fails(db, 'INSERT INTO source_rows VALUES (%s,3,\'{"value":"different"}\',%s,1)', (B, B))
    fails(db, "INSERT INTO source_rows VALUES (%s,3,'{\"value\":123}',null,null)", (B,))


def test_source_category_and_import_uniqueness(db):
    s = seed(db, publish=False)
    fails(
        db,
        "INSERT INTO "
        "interactions(person_id,content_id,session_id,event_type,event_at,file_"
        "sha256,row_number) VALUES "
        "('-9223372036854775808','10','1','VIEW',now(),%s,1)",
        (A,),
    )
    fails(
        db,
        "INSERT INTO "
        "article_events(content_id,event_type,event_at,file_sha256,row_number) "
        "VALUES ('10','CONTENT SHARED',now(),%s,1)",
        (A,),
    )
    assert s["events"]


def test_metadata_cannot_use_removal_wrong_content_or_damage_base(db):
    s = seed(db, publish=False)
    put(db, "source_rows", file_sha256=A, row_number=3, raw_fields={"type": "remove"})
    event = put(
        db,
        "article_events",
        content_id="10",
        event_type="CONTENT REMOVED",
        event_at=CUTOFF,
        file_sha256=A,
        row_number=3,
    )[0]
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        metadata(db, event)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        metadata(db, s["events"][0], content="2")
    put(
        db,
        "metadata_variants",
        variant_id="damage-v1",
        kind="damage",
        config={},
        config_sha256=A,
        seed=1,
    )
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        metadata(db, s["events"][0], variant="damage-v1", base=s["versions"][1])
    damaged = metadata(db, s["events"][0], variant="damage-v1", base=s["versions"][0])
    assert db.execute(
        "SELECT base_version_id FROM metadata_versions WHERE metadata_version_id=%s", (damaged,)
    ).fetchone() == (s["versions"][0],)


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE metadata_versions SET title='Changed'",
        "DELETE FROM metadata_versions",
        "DELETE FROM article_events",
        "UPDATE source_rows SET raw_fields='{}'",
        "TRUNCATE metadata_versions CASCADE",
        "DELETE FROM snapshots",
        "UPDATE snapshots SET status='building'",
        "DELETE FROM snapshot_articles",
        "INSERT INTO source_rows(file_sha256,row_number,raw_fields) VALUES ('" + A + "',99,'{}')",
    ],
)
def test_immutable_history_and_published_members(seeded, query):
    db, _ = seeded
    fails(db, query)


def test_snapshot_future_metadata_and_variant_mismatch(db):
    s = seed(db, publish=False)
    put(db, "source_rows", file_sha256=A, row_number=3, raw_fields={"type": "future"})
    event = put(
        db,
        "article_events",
        content_id="10",
        event_type="CONTENT SHARED",
        event_at=CUTOFF,
        file_sha256=A,
        row_number=3,
    )[0]
    future = metadata(db, event)
    fails(
        db,
        "UPDATE snapshot_articles SET metadata_version_id=%s WHERE "
        "snapshot_id=%s AND content_id='10'",
        (future, s["snapshot"]),
    )
    put(db, "metadata_variants", variant_id="damage", kind="damage", config={}, config_sha256=A)
    damaged = metadata(db, s["events"][0], variant="damage", base=s["versions"][0])
    fails(
        db,
        "UPDATE snapshot_articles SET metadata_version_id=%s WHERE "
        "snapshot_id=%s AND content_id='10'",
        (damaged, s["snapshot"]),
    )
    fails(db, "UPDATE snapshots SET status='published' WHERE snapshot_id=%s", (s["snapshot"],))
    db.execute(
        "UPDATE snapshot_articles SET availability='ambiguous' WHERE "
        "snapshot_id=%s AND content_id='10'",
        (s["snapshot"],),
    )
    db.execute("UPDATE snapshots SET status='published' WHERE snapshot_id=%s", (s["snapshot"],))


def test_incomplete_snapshot_rejected(db):
    s = seed(db, publish=False)
    db.execute("DELETE FROM snapshot_interactions WHERE snapshot_id=%s", (s["snapshot"],))
    fails(db, "UPDATE snapshots SET status='published' WHERE snapshot_id=%s", (s["snapshot"],))


def test_wrong_interaction_duplicate_and_exclusion_rejected(db):
    s = seed(db, publish=False)
    fails(
        db,
        "UPDATE snapshot_interactions SET duplicate_of_interaction_id=null WHERE interaction_id=%s",
        (s["interactions"][1],),
    )
    fails(
        db,
        "UPDATE snapshot_interactions SET exclusion_reason='removed' WHERE interaction_id=%s",
        (s["interactions"][0],),
    )


@pytest.mark.parametrize(
    "boundary,expected",
    [
        ("2016-11-30T00:00:00Z", "not_shared"),
        ("2016-12-01T00:00:00Z", "ambiguous"),
        ("2016-12-02T00:00:00Z", "available"),
        ("2016-12-03T00:00:00Z", "ambiguous"),
        ("2016-12-04T00:00:00Z", "removed"),
        ("2016-12-05T00:00:00Z", "ambiguous"),
        ("2016-12-06T00:00:00Z", "available"),
    ],
)
def test_share_remove_reshare_boundaries(db, boundary, expected):
    seed(db, publish=False)
    for n, time, kind in (
        (3, "2016-12-03", "CONTENT REMOVED"),
        (4, "2016-12-05", "CONTENT SHARED"),
    ):
        put(db, "source_rows", file_sha256=A, row_number=n, raw_fields={"event": str(n)})
        put(
            db,
            "article_events",
            content_id="10",
            event_type=kind,
            event_at=time,
            file_sha256=A,
            row_number=n,
        )
    assert db.execute(
        "SELECT availability_at('10',%s,%s)", (boundary, Jsonb({"files": [A, B]}))
    ).fetchone() == (expected,)


def test_quality_coverage_and_completed_run_immutability(seeded):
    db, s = seeded
    run, _ = quality(db, s, complete=False)
    fails(db, "UPDATE quality_results SET completeness_score='NaN' WHERE quality_run_id=%s", (run,))
    fails(db, "UPDATE quality_results SET completeness_score=0.5 WHERE quality_run_id=%s", (run,))
    coverage = {
        d: {"assessed_checks": 1, "passed_checks": 1, "unassessed_checks": 1}
        for d in ("completeness", "validity", "consistency", "informativeness")
    }
    fails(
        db,
        "UPDATE quality_results SET dimension_coverage=%s WHERE quality_run_id=%s",
        (Jsonb(coverage), run),
    )
    db.execute(
        "UPDATE quality_results SET dimension_coverage=%s, overall_score=null "
        "WHERE quality_run_id=%s",
        (Jsonb(coverage), run),
    )
    db.execute(
        "UPDATE quality_runs SET status='completed', "
        "completed_at=clock_timestamp() WHERE quality_run_id=%s",
        (run,),
    )
    fails(db, "DELETE FROM quality_results WHERE quality_run_id=%s", (run,))
    fails(db, "UPDATE quality_warnings SET reason='Rewritten' WHERE quality_run_id=%s", (run,))


def test_reviews_preserve_disagreement_and_idempotency_tuple(seeded):
    db, s = seeded
    run, warning = quality(db, s)

    def review(label="Alice", **extra):
        values = dict(
            warning_id=warning,
            quality_run_id=run,
            metadata_version_id=s["versions"][0],
            snapshot_id=s["snapshot"],
            reviewer_label=label,
            decision="confirm",
            client_request_id=uuid4(),
        )
        values.update(extra)
        return put(db, "review_decisions", **values)

    original = review()
    review("Bob", decision="reject")
    review("Alice", decision="reject")
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        review(client_request_id=original[7])
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        review(metadata_version_id=s["versions"][1])
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        review("   ")
    fails(db, "UPDATE review_decisions SET decision='reject'")
    fails(db, "DELETE FROM review_decisions")
    assert db.execute("SELECT count(*) FROM review_decisions").fetchone() == (3,)


def test_feature_and_model_cross_snapshot_links_and_fitting_cutoff(seeded):
    db, s = seeded
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        put(
            db,
            "feature_sets",
            snapshot_id=s["snapshot"],
            feature_version="test",
            artifact_uri="a",
            artifact_sha256=A,
            config={},
            fitted_before_at="2017-01-02T00:00:00Z",
        )
    feature = put(
        db,
        "feature_sets",
        snapshot_id=s["snapshot"],
        feature_version="test",
        artifact_uri="a",
        artifact_sha256=A,
        config={},
        fitted_before_at=CUTOFF,
    )[0]
    put(
        db,
        "feature_items",
        feature_set_id=feature,
        content_id="10",
        metadata_version_id=s["versions"][0],
        snapshot_id=s["snapshot"],
    )
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        model(db, s, protocol_id="wrong")
    model(db, s, feature_set_id=feature)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        put(
            db,
            "feature_items",
            feature_set_id=feature,
            content_id="2",
            metadata_version_id=s["versions"][1],
            snapshot_id=s["snapshot"],
        )
    fails(db, "UPDATE model_runs SET manifest='{}'")


def test_hybrid_dependencies_and_quality_matching(seeded):
    db, s = seeded
    branch = model(db, s)
    hybrid = model(
        db,
        s,
        complete=False,
        model_type="fixed_hybrid",
        manifest={"branches": [{"model_run_id": str(branch), "feature_set_id": None}]},
    )
    db.execute("UPDATE model_runs SET status='completed' WHERE model_run_id=%s", (hybrid,))
    invalid = model(
        db,
        s,
        complete=False,
        model_type="fixed_hybrid",
        manifest={"branches": [{"model_run_id": str(uuid4()), "feature_set_id": None}]},
    )
    fails(db, "UPDATE model_runs SET status='completed' WHERE model_run_id=%s", (invalid,))
    q, _ = quality(db, s, complete=False)
    dependent = model(db, s, complete=False, quality_run_id=q)
    fails(db, "UPDATE model_runs SET status='completed' WHERE model_run_id=%s", (dependent,))


def test_rankings_validate_order_duplicates_versions_and_history(seeded):
    db, s = seeded
    rankings = ranking(db, s)
    entry(db, s, rankings, "2", 1)
    entry(db, s, rankings, "10", 2)
    fails(db, "UPDATE ranking_sets SET status='published' WHERE ranking_set_id=%s", (rankings,))
    db.execute("DELETE FROM prepared_rankings WHERE ranking_set_id=%s", (rankings,))
    entry(db, s, rankings, "10", 1)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        entry(db, s, rankings, "10", 2)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        entry(db, s, rankings, "2", 2, score=float("nan"))
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        entry(db, s, rankings, "2", 2, metadata_version_id=s["versions"][0])
    entry(db, s, rankings, "2", 2)
    db.execute("UPDATE ranking_sets SET status='published' WHERE ranking_set_id=%s", (rankings,))
    fails(db, "DELETE FROM prepared_rankings WHERE ranking_set_id=%s", (rankings,))
    fails(db, "UPDATE ranking_sets SET status='building' WHERE ranking_set_id=%s", (rankings,))


def test_similar_ranking_subject_and_reference_exclusion(seeded):
    db, s = seeded
    rankings = ranking(db, s, kind="similar")
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        entry(db, s, rankings)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        entry(db, s, rankings, person_id=None, reference_content_id="10")
    entry(db, s, rankings, "2", person_id=None, reference_content_id="10")
    db.execute("UPDATE ranking_sets SET status='published' WHERE ranking_set_id=%s", (rankings,))


def test_additional_snapshot_cannot_cross_wire_versions_or_runs(seeded):
    db, s = seeded
    another = put(
        db,
        "snapshots",
        protocol_id="deskdrop-temporal-v1",
        as_of_at=CUTOFF,
        source_manifest={"files": [A, B]},
        transform_version="another-v1",
        metadata_variant_id="original",
    )[0]
    for content, version in zip(("10", "2"), s["versions"], strict=True):
        put(
            db,
            "snapshot_articles",
            snapshot_id=another,
            content_id=content,
            metadata_version_id=version,
            availability="available",
        )
    for n, interaction in enumerate(s["interactions"]):
        put(
            db,
            "snapshot_interactions",
            snapshot_id=another,
            interaction_id=interaction,
            duplicate_of_interaction_id=s["interactions"][0] if n else None,
        )
    db.execute("UPDATE snapshots SET status='published' WHERE snapshot_id=%s", (another,))
    other = {**s, "snapshot": another}
    run, _ = quality(db, s)
    other_run, _ = quality(db, other, complete=False)
    fails(
        db,
        "UPDATE quality_results SET snapshot_id=%s WHERE quality_run_id=%s",
        (s["snapshot"], other_run),
    )
    feature = put(
        db,
        "feature_sets",
        snapshot_id=s["snapshot"],
        feature_version="v1",
        artifact_uri="local/features",
        artifact_sha256=A,
        config={},
        fitted_before_at=CUTOFF,
    )[0]
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        put(
            db,
            "feature_items",
            feature_set_id=feature,
            content_id="10",
            metadata_version_id=s["versions"][0],
            snapshot_id=another,
        )
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        model(db, other, feature_set_id=feature)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        model(db, other, quality_run_id=run)
    branch = model(db, s)
    hybrid = model(
        db,
        other,
        complete=False,
        model_type="fixed_hybrid",
        manifest={"branches": [{"model_run_id": str(branch), "feature_set_id": None}]},
    )
    fails(db, "UPDATE model_runs SET status='completed' WHERE model_run_id=%s", (hybrid,))
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        ranking(db, other, model_run_id=branch)
    rankings = ranking(db, s)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        entry(db, s, rankings, snapshot_id=another)


def test_new_damage_version_keeps_published_original_and_review_history(seeded):
    db, s = seeded
    run, warning = quality(db, s)
    put(
        db,
        "review_decisions",
        warning_id=warning,
        quality_run_id=run,
        metadata_version_id=s["versions"][0],
        snapshot_id=s["snapshot"],
        reviewer_label="Alice",
        decision="confirm",
        client_request_id=uuid4(),
    )
    put(
        db,
        "metadata_variants",
        variant_id="damage-later",
        kind="damage",
        config={"mask_title": True},
        config_sha256=B,
        seed=42,
    )
    damaged = metadata(db, s["events"][0], variant="damage-later", base=s["versions"][0])
    assert damaged != s["versions"][0]
    assert db.execute(
        "SELECT metadata_version_id FROM snapshot_articles "
        "WHERE snapshot_id=%s AND content_id='10'",
        (s["snapshot"],),
    ).fetchone() == (s["versions"][0],)
    assert db.execute("SELECT metadata_version_id FROM review_decisions").fetchone() == (
        s["versions"][0],
    )
    assert db.execute("SELECT count(*) FROM metadata_versions").fetchone() == (3,)


def test_quality_dependent_model_requires_matching_feature_results(seeded):
    db, s = seeded
    run, _ = quality(db, s)
    feature = put(
        db,
        "feature_sets",
        snapshot_id=s["snapshot"],
        feature_version="v1",
        artifact_uri="local/features",
        artifact_sha256=A,
        config={},
        fitted_before_at=CUTOFF,
    )[0]
    for content, version in zip(("10", "2"), s["versions"], strict=True):
        put(
            db,
            "feature_items",
            feature_set_id=feature,
            content_id=content,
            metadata_version_id=version,
            snapshot_id=s["snapshot"],
        )
    model(
        db,
        s,
        model_type="quality_hybrid",
        feature_set_id=feature,
        quality_run_id=run,
        manifest={"branches": [], "fallback_reason": "test-only fallback"},
    )
    partial, _ = quality(db, s, complete=False)
    db.execute(
        "DELETE FROM quality_results WHERE quality_run_id=%s AND metadata_version_id=%s",
        (partial, s["versions"][1]),
    )
    db.execute(
        "UPDATE quality_runs SET status='completed', completed_at=clock_timestamp() "
        "WHERE quality_run_id=%s",
        (partial,),
    )
    missing = model(db, s, complete=False, quality_run_id=partial, feature_set_id=feature)
    fails(db, "UPDATE model_runs SET status='completed' WHERE model_run_id=%s", (missing,))


def test_same_second_conflict_persists_until_unambiguous_lifecycle(db):
    seed(db, publish=False)
    for n, time, kind in (
        (3, "2016-12-03", "CONTENT REMOVED"),
        (4, "2016-12-03", "CONTENT SHARED"),
        (5, "2016-12-05", "CONTENT SHARED"),
    ):
        put(db, "source_rows", file_sha256=A, row_number=n, raw_fields={"event": str(n)})
        put(
            db,
            "article_events",
            content_id="10",
            event_type=kind,
            event_at=time,
            file_sha256=A,
            row_number=n,
        )
    for boundary, expected in (("2016-12-04", "ambiguous"), ("2016-12-06", "available")):
        assert db.execute(
            "SELECT availability_at('10',%s,%s)", (boundary, Jsonb({"files": [A, B]}))
        ).fetchone() == (expected,)


@pytest.mark.parametrize("score", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_rank_scores_rejected(seeded, score):
    db, s = seeded
    rankings = ranking(db, s)
    with pytest.raises(psycopg.IntegrityError), db.transaction():
        entry(db, s, rankings, score=score)
