"""Real publication, fixed holdouts and metadata/history leakage checks."""

import csv
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from cmdl.data import snapshots
from cmdl.data.ingest import digest, ingest
from cmdl.data.profile import ARTICLE_COLUMNS, INTERACTION_COLUMNS
from cmdl.data.temporal import TRAIN_END, VALIDATION_END, TemporalError
from cmdl.db.migrate import migrate


def instant(day):
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


@pytest.fixture
def imported(empty_db, tmp_path):
    migrate(empty_db)
    raw = tmp_path / "raw"
    raw.mkdir()
    articles = [
        ("2016-12-01", "CONTENT SHARED", "10", "A old"),
        ("2017-01-10", "CONTENT REMOVED", "10", "Removal copied text"),
        ("2017-01-20", "CONTENT SHARED", "10", "A new"),
        ("2016-12-01", "CONTENT SHARED", "2", "B"),
        ("2017-01-15", "CONTENT REMOVED", "2", "B removal"),
        ("2017-01-10", "CONTENT SHARED", "3", "New January"),
        ("2016-12-01", "CONTENT SHARED", "4", "Removed before request"),
        ("2016-12-25", "CONTENT REMOVED", "4", "Removal"),
        ("2016-12-01", "CONTENT SHARED", "5", "Before conflict"),
        ("2016-12-30", "CONTENT SHARED", "5", "Conflicting share"),
        ("2016-12-30", "CONTENT REMOVED", "5", "Conflicting removal"),
        ("2017-01-25", "CONTENT SHARED", "5", "Restored"),
        ("2017-02-02", "CONTENT SHARED", "6", "Future February"),
        ("2016-12-02", "CONTENT REMOVED", "7", "Removal only"),
    ]
    interactions = [
        ("2016-12-10", "VIEW", "10", "100"),
        ("2016-12-10", "VIEW", "10", "100"),
        ("2017-01-01", "LIKE", "2", "100"),
        ("2017-01-02", "COMMENT CREATED", "2", "100"),
        ("2017-01-05", "VIEW", "10", "100"),
        ("2017-01-11", "VIEW", "3", "200"),
        ("2017-01-10", "VIEW", "10", "200"),
        ("2017-01-16", "BOOKMARK", "2", "200"),
        ("2017-02-01", "VIEW", "2", "300"),
        ("2017-02-02", "VIEW", "3", "300"),
        ("2017-02-03", "VIEW", "6", "300"),
        ("2017-03-01", "VIEW", "3", "300"),
        ("2017-01-22", "FOLLOW", "10", "400"),
        ("2017-02-02", "VIEW", "10", "400"),
        ("2016-12-03", "VIEW", "7", "500"),
        ("2016-11-30", "VIEW", "10", "600"),
    ]
    entries = []
    for name, columns, records in (
        ("shared_articles.csv", ARTICLE_COLUMNS, articles),
        ("users_interactions.csv", INTERACTION_COLUMNS, interactions),
    ):
        path = raw / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for day, kind, content, last in records:
                row = dict.fromkeys(columns, "")
                row.update({key: "1" for key in columns if key.endswith("Id")})
                row.update(
                    timestamp=str(int(instant(day).timestamp())), eventType=kind, contentId=content
                )
                if name == "shared_articles.csv":
                    row.update(
                        title=last,
                        text=last,
                        url="https://example.org",
                        lang="en",
                        contentType="HTML",
                    )
                else:
                    row.update(personId=last)
                writer.writerow(row)
        entries.append(
            {
                "file": name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (raw / "manifest.json").write_text(
        json.dumps({"source_url": "https://example.org", "upstream_version": 5, "files": entries})
    )
    report, _ = ingest(empty_db, raw)
    return empty_db, UUID(report["import_id"])


def test_official_snapshots_replay_partition_and_as_of_metadata(imported, tmp_path):
    db, import_id = imported
    report, created = snapshots.prepare_benchmark(db, import_id)
    assert created
    assert snapshots.prepare_benchmark(db, import_id) == (report, False)
    validation = snapshots.load_snapshot(db, UUID(report["snapshots"]["validation"]["snapshot_id"]))
    test = snapshots.load_snapshot(db, UUID(report["snapshots"]["test"]["snapshot_id"]))
    assert validation.as_of_at == TRAIN_END and test.as_of_at == VALIDATION_END
    assert [a.content_id for a in validation.candidates()] == ["10", "2"]
    assert [a.content_id for a in test.candidates()] == ["10", "3", "5"]
    assert validation.candidates("100")[0].content_id == "2"
    assert validation.candidates("600") == tuple(
        a for a in validation.candidates() if a.content_id != "10"
    )
    assert validation.user_history("600") == ()  # Pre-share reads do not become fitting examples.
    assert next(a for a in validation.articles if a.content_id == "10").metadata.title == "A old"
    assert next(a for a in test.articles if a.content_id == "10").metadata.title == "A new"
    assert test.user_history("100")[0].metadata.title == "A old"
    assert next(a for a in validation.articles if a.content_id == "6").metadata is None
    assert "300" not in test.known_users
    assert all(
        i.event_at < view.as_of_at and i.metadata.shared_at < i.event_at
        for view in (validation, test)
        for i in view.history
    )
    assert report["raw_event_partitions"] == {
        "train": 4,
        "validation": 7,
        "test": 4,
        "after_test": 1,
    }
    assert report["holdouts"]["validation"]["event_partition"] == {
        "source_duplicate": 0,
        "unresolved": 0,
        "removal_only": 0,
        "pre_share": 0,
        "ambiguous": 1,
        "removed": 1,
        "outside_fixed_catalog": 1,
        "previously_consumed": 1,
        "eligible_event": 3,
    }
    assert report["holdouts"]["validation"]["distinct_relevant_pairs"] == 2
    assert report["holdouts"]["test"]["event_partition"]["outside_fixed_catalog"] == 1
    assert report["holdouts"]["test"]["distinct_relevant_pairs"] == 1
    assert report["holdouts"]["test"]["users_without_eligible_positives"] == 1
    output = tmp_path / "reports"
    snapshots.write_benchmark_report(report, output)
    first = (output / "benchmark.json").read_bytes()
    snapshots.write_benchmark_report(report, output)
    assert first == (output / "benchmark.json").read_bytes()
    assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (2,)


def test_two_snapshot_publication_is_atomic(imported, monkeypatch):
    db, import_id = imported
    store = snapshots._store_members
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        store(*args)
        if calls == 2:
            raise TemporalError("Forced publication failure")

    monkeypatch.setattr(snapshots, "_store_members", fail_second)
    with pytest.raises(TemporalError):
        snapshots.prepare_benchmark(db, import_id)
    assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM snapshot_articles").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM snapshot_interactions").fetchone() == (0,)


def test_concurrent_snapshot_publication_reuses_exact_ids(imported):
    db, import_id = imported
    options = db.info.options

    def run():
        with psycopg.connect(os.environ["CMDL_TEST_DSN"], options=options) as conn:
            return snapshots.prepare_benchmark(conn, import_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (2,)


def test_missing_variant_is_atomic_and_never_substitutes_original(imported):
    db, import_id = imported
    db.execute(
        "INSERT INTO metadata_variants VALUES ('damage','damage','{}',%s,1,now())", (digest({}),)
    )
    db.commit()
    with pytest.raises(TemporalError, match="missing a historical share version"):
        snapshots.prepare_benchmark(db, import_id, "damage")
    assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (0,)


def test_unknown_import_variant_and_snapshot_fail_closed(imported):
    db, import_id = imported
    with pytest.raises(TemporalError):
        snapshots.prepare_benchmark(db, uuid4())
    with pytest.raises(TemporalError):
        snapshots.prepare_benchmark(db, import_id, "unknown")
    with pytest.raises(TemporalError):
        snapshots.load_snapshot(db, uuid4())
    with pytest.raises(TemporalError):
        snapshots.publish_snapshot(db, import_id, datetime(2017, 1, 1))


def test_unpublished_snapshot_cannot_be_model_input(imported):
    db, import_id = imported
    hashes = db.execute(
        "SELECT article_file_sha256,interaction_file_sha256 FROM source_imports WHERE import_id=%s",
        (import_id,),
    ).fetchone()
    snapshot_id = db.execute(
        """INSERT INTO snapshots(protocol_id,as_of_at,source_manifest,transform_version,
            metadata_variant_id)
        VALUES ('deskdrop-temporal-v1',%s,%s,'test','original') RETURNING snapshot_id""",
        (TRAIN_END, Jsonb({"files": list(hashes)})),
    ).fetchone()[0]
    with pytest.raises(TemporalError):
        snapshots.load_snapshot(db, snapshot_id)


def test_full_deskdrop_snapshots_and_model_loader(empty_db):
    migrate(empty_db)
    imported, _ = ingest(empty_db, Path("data/raw"))
    report, created = snapshots.prepare_benchmark(empty_db, UUID(imported["import_id"]))
    assert created
    assert report["raw_event_partitions"] == {
        "train": 66508,
        "validation": 2895,
        "test": 2909,
        "after_test": 0,
    }
    assert snapshots.prepare_benchmark(empty_db, UUID(imported["import_id"])) == (report, False)
    for name, (start, _end) in {
        "validation": (TRAIN_END, VALIDATION_END),
        "test": (VALIDATION_END, instant("2017-03-01")),
    }.items():
        stats = report["snapshots"][name]
        view = snapshots.load_snapshot(empty_db, UUID(stats["snapshot_id"]))
        assert len(view.history) == stats["history_partition"]["canonical_eligible"]
        assert len(view.candidates()) == stats["catalog_records"]
        assert all(i.event_at < start and i.metadata.shared_at < i.event_at for i in view.history)
        assert all(a.metadata.shared_at < start for a in view.candidates())
        assert (
            sum(report["holdouts"][name]["event_partition"].values())
            == report["raw_event_partitions"][name]
        )
        person = view.history[0].person_id
        consumed = {content for user, content in view.consumed_pairs if user == person}
        assert consumed
        assert all(a.content_id not in consumed for a in view.candidates(person))


@pytest.mark.parametrize(
    "day,state,title",
    [
        ("2017-01-10", "ambiguous", "A old"),
        ("2017-01-16", "removed", "A old"),
        ("2017-01-20", "ambiguous", "A old"),
        ("2017-01-21", "available", "A new"),
    ],
)
def test_published_boundary_states_do_not_rewrite_prior_consumption(imported, day, state, title):
    db, import_id = imported
    report, _ = snapshots.publish_snapshot(db, import_id, instant(day))
    view = snapshots.load_snapshot(db, UUID(report["snapshot_id"]))
    a = next(a for a in view.articles if a.content_id == "10")
    assert a.availability == state and a.metadata.title == title
    assert all(a.content_id != "10" for a in view.candidates("100"))
    assert view.user_history("100")[0].metadata.title == "A old"


def test_variant_eligibility_is_fixed_and_future_metadata_does_not_change_past_id(imported):
    db, import_id = imported
    original, _ = snapshots.prepare_benchmark(db, import_id)
    config = {"experiment": "test"}
    db.execute(
        "INSERT INTO metadata_variants(variant_id,kind,config,config_sha256,seed) "
        "VALUES ('damage','damage',%s,%s,1)",
        (Jsonb(config), digest(config)),
    )
    versions = db.execute("""
        SELECT v.metadata_version_id,v.content_id,v.share_event_id,e.event_at,v.title,v.text,
            v.url,v.lang,v.content_type
        FROM metadata_versions v JOIN article_events e ON v.share_event_id=e.article_event_id
        WHERE v.variant_id='original' ORDER BY e.event_at
    """).fetchall()

    def add(group):
        for version, content, event, _at, title, text, url, lang, kind in group:
            payload = {
                "title": "damage:" + title,
                "text": text,
                "url": url,
                "lang": lang,
                "content_type": kind,
            }
            db.execute(
                """
                INSERT INTO metadata_versions(content_id,share_event_id,variant_id,base_version_id,
                    title,text,url,lang,content_type,payload_sha256)
                VALUES (%s,%s,'damage',%s,%s,%s,%s,%s,%s,%s)
            """,
                (content, event, version, payload["title"], text, url, lang, kind, digest(payload)),
            )

    add([v for v in versions if v[3] < TRAIN_END])
    db.commit()
    early, _ = snapshots.publish_snapshot(db, import_id, TRAIN_END, "damage")
    add([v for v in versions if v[3] >= TRAIN_END])
    db.commit()
    assert snapshots.publish_snapshot(db, import_id, TRAIN_END, "damage") == (early, False)
    damaged, _ = snapshots.prepare_benchmark(db, import_id, "damage")
    assert original["holdouts"] == damaged["holdouts"]
    for name in ("validation", "test"):
        old = snapshots.load_snapshot(db, UUID(original["snapshots"][name]["snapshot_id"]))
        new = snapshots.load_snapshot(db, UUID(damaged["snapshots"][name]["snapshot_id"]))
        assert old.snapshot_id != new.snapshot_id
        assert [a.content_id for a in old.candidates()] == [a.content_id for a in new.candidates()]
        assert all(a.metadata.title.startswith("damage:") for a in new.candidates())


def test_loader_normalizes_session_timezone(imported):
    db, import_id = imported
    report, _ = snapshots.prepare_benchmark(db, import_id)
    db.execute("SET TIME ZONE 'Asia/Shanghai'")
    view = snapshots.load_snapshot(db, UUID(report["snapshots"]["test"]["snapshot_id"]))
    assert view.as_of_at.utcoffset().total_seconds() == 0
    assert all(i.event_at.utcoffset().total_seconds() == 0 for i in view.history)
    assert all(i.metadata.shared_at.utcoffset().total_seconds() == 0 for i in view.history)


def test_invalid_variant_recipe_checksum_is_rejected(imported):
    db, import_id = imported
    db.execute("INSERT INTO metadata_variants VALUES ('bad','damage','{}',%s,1,now())", ("d" * 64,))
    db.commit()
    with pytest.raises(TemporalError, match="configuration checksum"):
        snapshots.prepare_benchmark(db, import_id, "bad")
    assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (0,)
