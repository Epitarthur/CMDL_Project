"""Source preservation, import atomicity and modeling-exclusion reconciliation."""

import csv
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from cmdl.cli import main
from cmdl.data import ingest as loader
from cmdl.data.profile import ARTICLE_COLUMNS, INTERACTION_COLUMNS, ProfileError
from cmdl.db.migrate import migrate

BIG = "9223372036854775807"


def refresh_manifest(raw):
    value = json.loads((raw / "manifest.json").read_text())
    for item in value["files"]:
        payload = (raw / item["file"]).read_bytes()
        item.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    (raw / "manifest.json").write_text(json.dumps(value))


@pytest.fixture
def raw(tmp_path):
    path = tmp_path / "raw"
    path.mkdir()
    for name, columns, events in (
        (
            "shared_articles.csv",
            ARTICLE_COLUMNS,
            [
                (10, "CONTENT SHARED", BIG),
                (20, "CONTENT REMOVED", BIG),
                (30, "CONTENT SHARED", BIG),
                (5, "CONTENT REMOVED", "2"),
            ],
        ),
        (
            "users_interactions.csv",
            INTERACTION_COLUMNS,
            [
                (9, "VIEW", BIG),
                (10, "VIEW", BIG),
                (15, "LIKE", BIG),
                (25, "BOOKMARK", BIG),
                (35, "FOLLOW", BIG),
                (8, "COMMENT CREATED", "2"),
                (8, "VIEW", "3"),
                (15, "LIKE", BIG),
            ],
        ),
    ):
        with (path / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for timestamp, event, content in events:
                row = dict.fromkeys(columns, "")
                row.update({key: "1" for key in columns if key.endswith("Id")})
                row.update(timestamp=str(timestamp), eventType=event, contentId=content)
                if name == "shared_articles.csv":
                    row.update(
                        authorPersonId="-9223372036854775808",
                        authorSessionId="-1",
                        title="  Café — original title  ",
                        text="First line\r\nSecond\nline",
                        url="https://example.org/",
                        lang="en",
                        contentType="HTML",
                    )
                else:
                    row.update(personId="9007199254740993", sessionId="-9223372036854775808")
                writer.writerow(row)
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "source_url": "https://example.org/deskdrop",
                "upstream_version": 5,
                "files": [
                    {"file": name} for name in ("shared_articles.csv", "users_interactions.csv")
                ],
            }
        )
    )
    refresh_manifest(path)
    return path


@pytest.fixture
def database(empty_db):
    migrate(empty_db)
    return empty_db


def totals(db):
    return tuple(
        db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        for name in (
            "source_files",
            "source_rows",
            "users",
            "articles",
            "article_events",
            "interactions",
            "metadata_versions",
            "source_imports",
        )
    )


def test_import_repeat_preserves_source_strings_metadata_and_lineage(database, raw, tmp_path):
    report, created = loader.ingest(database, raw)
    assert created
    before = totals(database)
    assert before == (2, 12, 2, 3, 4, 8, 2, 1)
    repeat, created = loader.ingest(database, raw)
    assert not created and repeat == report
    assert totals(database) == before
    assert report["interaction_partition"] == {
        "source_duplicate": 1,
        "unresolved": 1,
        "removal_only": 1,
        "pre_share": 1,
        "ambiguous": 1,
        "removed": 1,
        "canonical_eligible": 2,
    }
    assert report["identities"] == {
        "authors": 1,
        "interacting_users": 1,
        "user_union": 2,
        "article_union": 3,
        "removal_only_articles": 1,
        "interaction_only_articles": 1,
    }
    assert database.execute("SELECT title,text FROM metadata_versions LIMIT 1").fetchone() == (
        "  Café — original title  ",
        "First line\r\nSecond\nline",
    )
    assert database.execute(
        "SELECT count(*) FROM metadata_versions WHERE content_id='2'"
    ).fetchone() == (0,)
    assert database.execute("SELECT count(*) FROM articles WHERE content_id='3'").fetchone() == (1,)
    assert database.execute(
        "SELECT raw_fields->>'text' FROM source_rows WHERE raw_fields ? 'text' LIMIT 1"
    ).fetchone() == ("First line\r\nSecond\nline",)
    assert database.execute("SELECT session_id FROM interactions LIMIT 1").fetchone() == (
        "-9223372036854775808",
    )
    assert report["audit_rows"][-1]["duplicate_of_row_number"] == 3
    assert report["audit_rows"][-1]["exclusion_reason"] is None
    output = tmp_path / "audit"
    loader.write_import_report(report, output, raw)
    first = (output / "import.json").read_bytes()
    loader.write_import_report(repeat, output, raw)
    assert first == (output / "import.json").read_bytes()
    assert json.loads(first) == report


@pytest.mark.parametrize(
    "change", ["hash", "header", "extra_field", "event", "id", "timestamp", "quote", "nul"]
)
def test_invalid_sources_fail_before_any_database_write(database, raw, change):
    path = raw / "users_interactions.csv"
    text = path.read_text()
    if change == "hash":
        path.write_text(text + "changed")
    else:
        if change == "header":
            text = text.replace("timestamp,eventType", "wrong,eventType", 1)
        elif change == "extra_field":
            text = text.replace("9,VIEW", "extra,9,VIEW", 1)
        elif change == "event":
            text = text.replace("9,VIEW", "9,UNKNOWN", 1)
        elif change == "id":
            text = text.replace(BIG, "9223372036854775807.0", 1)
        elif change == "timestamp":
            text = text.replace("9,VIEW", "not-time,VIEW", 1)
        elif change == "quote":
            text += '"unterminated'
        elif change == "nul":
            text = text.replace("9,VIEW", "9,VI\x00EW", 1)
        path.write_text(text)
        refresh_manifest(raw)
    with pytest.raises(ProfileError):
        loader.ingest(database, raw)
    assert totals(database) == (0,) * 8


def test_failure_after_both_files_write_rolls_back_everything(database, raw, monkeypatch):
    def fail(db, *args):
        assert db.execute("SELECT count(*) FROM interactions").fetchone() == (8,)
        raise loader.IngestError("Forced reconciliation failure")

    monkeypatch.setattr(loader, "_report", fail)
    with pytest.raises(loader.IngestError):
        loader.ingest(database, raw)
    assert totals(database) == (0,) * 8
    assert database.execute("SELECT count(*) FROM metadata_variants").fetchone() == (0,)


def test_provenance_drift_and_failed_later_attempt_preserve_import(database, raw):
    report, _ = loader.ingest(database, raw)
    before = totals(database)
    manifest = json.loads((raw / "manifest.json").read_text())
    manifest["upstream_version"] = 6
    (raw / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(loader.IngestError, match="provenance"):
        loader.ingest(database, raw)
    assert totals(database) == before
    assert database.execute("SELECT report FROM source_imports").fetchone() == (report,)


def test_concurrent_imports_produce_one_committed_import(database, raw):
    options = database.info.options

    def apply():
        with psycopg.connect(os.environ["CMDL_TEST_DSN"], options=options) as other:
            return loader.ingest(other, raw)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply(), range(2)))
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    assert totals(database) == (2, 12, 2, 3, 4, 8, 2, 1)


def test_replay_after_publication_and_shared_file_in_new_bundle(database, raw):
    report, _ = loader.ingest(database, raw)
    hashes = [
        report["sources"][name]["sha256"]
        for name in ("shared_articles.csv", "users_interactions.csv")
    ]
    snapshot = database.execute(
        """
        INSERT INTO snapshots(protocol_id,as_of_at,source_manifest,transform_version,
            metadata_variant_id)
        VALUES ('deskdrop-temporal-v1',to_timestamp(40),%s,'test','original') RETURNING snapshot_id
    """,
        (Jsonb({"files": hashes}),),
    ).fetchone()[0]
    for content in (BIG, "2", "3"):
        version = database.execute(
            """
            SELECT metadata_version_id FROM metadata_versions v JOIN article_events e
                ON v.share_event_id=e.article_event_id
            WHERE v.content_id=%s ORDER BY e.event_at DESC LIMIT 1
        """,
            (content,),
        ).fetchone()
        database.execute(
            """
            INSERT INTO snapshot_articles(snapshot_id,content_id,metadata_version_id,availability)
            VALUES (%s,%s,%s,availability_at(%s,to_timestamp(40),%s))
        """,
            (snapshot, content, version[0] if version else None, content, Jsonb({"files": hashes})),
        )
    database.execute(
        """
        INSERT INTO snapshot_interactions(snapshot_id,interaction_id,duplicate_of_interaction_id,
            exclusion_reason)
        SELECT %s,i.interaction_id,c.interaction_id,interaction_exclusion(i,%s)
        FROM interactions i JOIN source_rows r USING(file_sha256,row_number)
        LEFT JOIN interactions c ON c.file_sha256=r.duplicate_file_sha256
            AND c.row_number=r.duplicate_row_number
    """,
        (snapshot, Jsonb({"files": hashes})),
    )
    database.execute("UPDATE snapshots SET status='published' WHERE snapshot_id=%s", (snapshot,))
    database.commit()
    assert loader.ingest(database, raw) == (report, False)
    with (raw / "users_interactions.csv").open("a") as handle:
        handle.write(f"36,VIEW,{BIG},9007199254740993,-9223372036854775808,,,\n")
    refresh_manifest(raw)
    new, created = loader.ingest(database, raw)
    assert created and new["import_id"] != report["import_id"]
    assert totals(database) == (3, 21, 2, 3, 4, 17, 2, 2)
    assert database.execute(
        "SELECT status FROM snapshots WHERE snapshot_id=%s", (snapshot,)
    ).fetchone() == ("published",)


def test_untracked_existing_file_is_not_silently_repaired(database, raw):
    manifest = json.loads((raw / "manifest.json").read_text())
    item = manifest["files"][0]
    database.execute(
        """
        INSERT INTO source_files(sha256,filename,bytes,dataset_ref,upstream_version,event_category)
        VALUES (%s,%s,%s,%s,'5','article')
    """,
        (item["sha256"], item["file"], item["bytes"], manifest["source_url"]),
    )
    database.commit()
    with pytest.raises(loader.IngestError, match="no completed import"):
        loader.ingest(database, raw)
    assert totals(database) == (1, 0, 0, 0, 0, 0, 0, 0)


def test_full_deskdrop_import_and_replay_reconcile(database):
    report, created = loader.ingest(database, Path("data/raw"))
    assert created
    assert report["stored"] == {
        "source_rows": 75434,
        "article_events": 3122,
        "original_metadata_versions": 3047,
        "interactions": 72312,
    }
    assert report["identities"]["interacting_users"] == 1895
    assert report["identities"]["removal_only_articles"] == 10
    assert report["interaction_temporal_counts"] == {
        "unresolved": 0,
        "removal_only": 43,
        "pre_share": 95,
        "ambiguous": 12,
        "removed": 16,
        "no_temporal_exclusion": 72146,
    }
    assert report["interaction_partition"]["source_duplicate"] == 11
    assert sum(report["interaction_partition"].values()) == 72312
    before = totals(database)
    assert loader.ingest(database, Path("data/raw")) == (report, False)
    assert totals(database) == before


def test_output_protection_and_failure_diagnostic(raw, tmp_path):
    with pytest.raises(loader.IngestError):
        loader.validate_output(raw / "audit", raw)
    output = tmp_path / "audit"
    loader.write_failure(output, raw, "input_or_provenance", "Malformed input")
    failure = json.loads(next((output / "failures").glob("*.json")).read_text())
    assert failure["stage"] == "input_or_provenance"
    assert not (output / "import.json").exists()


def test_cli_does_not_echo_connection_secrets(raw, tmp_path, monkeypatch, capsys):
    def fail(**kwargs):
        raise psycopg.OperationalError("password=super-secret")

    monkeypatch.setattr(psycopg, "connect", fail)
    output = tmp_path / "output"
    assert main(["ingest", "--raw-dir", str(raw), "--output-dir", str(output)]) == 1
    assert "super-secret" not in capsys.readouterr().err
    assert "super-secret" not in next((output / "failures").glob("*.json")).read_text()


def test_duplicate_exclusion_overlap_is_counted_once_in_partition(database, raw):
    path = raw / "users_interactions.csv"
    lines = path.read_text().splitlines()
    with path.open("a") as handle:
        handle.write(lines[1] + "\n")
    refresh_manifest(raw)
    report, _ = loader.ingest(database, raw)
    assert report["interaction_temporal_counts"]["pre_share"] == 2
    assert report["interaction_partition"]["pre_share"] == 1
    assert report["interaction_partition"]["source_duplicate"] == 2
    assert sum(report["interaction_partition"].values()) == 9
    assert report["audit_rows"][-1]["duplicate_of_row_number"] == 1
    assert report["audit_rows"][-1]["exclusion_reason"] == "pre_share"


def test_export_failure_keeps_committed_audit_and_replay_restores_files(
    database, raw, tmp_path, monkeypatch, capsys
):
    connect = psycopg.connect
    options = database.info.options
    monkeypatch.setattr(
        psycopg, "connect", lambda **_: connect(os.environ["CMDL_TEST_DSN"], options=options)
    )
    writer = loader.write_import_report

    def broken(*args):
        raise OSError("Output disk unavailable")

    monkeypatch.setattr(loader, "write_import_report", broken)
    output = tmp_path / "output"
    args = ["ingest", "--raw-dir", str(raw), "--output-dir", str(output)]
    assert main(args) == 1
    assert "committed" in capsys.readouterr().err
    assert totals(database) == (2, 12, 2, 3, 4, 8, 2, 1)
    assert not (output / "import.json").exists()
    monkeypatch.setattr(loader, "write_import_report", writer)
    assert main(args) == 0
    assert "Already imported" in capsys.readouterr().out
    assert (output / "import.json").exists()
    assert totals(database) == (2, 12, 2, 3, 4, 8, 2, 1)
