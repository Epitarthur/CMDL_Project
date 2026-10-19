"""Contract and temporal edge cases on small chronological source fixtures."""

import csv
import hashlib
import json

import pytest

from cmdl.cli import main
from cmdl.data.profile import (
    ARTICLE_COLUMNS,
    INTERACTION_COLUMNS,
    ProfileError,
    profile,
    write_report,
)

BIG_ID = "9223372036854775807"


def sources(tmp_path, articles=None, interactions=None):
    raw = tmp_path / "raw"
    raw.mkdir()
    if articles is None:
        articles = [
            (10, "CONTENT SHARED", BIG_ID),
            (20, "CONTENT REMOVED", BIG_ID),
            (30, "CONTENT SHARED", BIG_ID),
            (5, "CONTENT REMOVED", "2"),
        ]
    if interactions is None:
        interactions = [
            (9, BIG_ID),
            (10, BIG_ID),
            (15, BIG_ID),
            (25, BIG_ID),
            (35, BIG_ID),
            (8, "2"),
            (8, "3"),
            (15, BIG_ID),
        ]
    entries = []
    for name, columns, records in [
        ("shared_articles.csv", ARTICLE_COLUMNS, articles),
        ("users_interactions.csv", INTERACTION_COLUMNS, interactions),
    ]:
        path = raw / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for record in records:
                row = dict.fromkeys(columns, "")
                row.update({column: "1" for column in columns if column.endswith("Id")})
                row.update(timestamp=str(record[0]), contentId=record[-1])
                row["eventType"] = record[1] if len(record) == 3 else "VIEW"
                writer.writerow(row)
        entries.append(
            {
                "file": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    (raw / "manifest.json").write_text(json.dumps({"files": entries}))
    return raw


def refresh_manifest(raw):
    manifest = json.loads((raw / "manifest.json").read_text())
    for entry in manifest["files"]:
        content = (raw / entry["file"]).read_bytes()
        entry.update(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
    (raw / "manifest.json").write_text(json.dumps(manifest))


def test_chronology_overlap_and_exact_ids(tmp_path):
    raw = sources(tmp_path)
    report = profile(raw)
    flags = report["interaction_anomalies"]
    assert flags["before_first_share"]["rows"] == 1
    assert flags["before_first_share"]["examples"][0]["content_id"] == BIG_ID
    assert flags["same_timestamp_as_article_event"]["rows"] == 1
    assert flags["after_removal_before_reshare"]["rows"] == 2  # Includes removal-only ID.
    assert flags["removal_only_content_id"]["rows"] == 1
    assert flags["unresolved_content_id"]["rows"] == 1
    assert sum(report["interaction_anomaly_combinations"].values()) == 8
    assert report["files"]["users_interactions.csv"]["exact_duplicate_excess_rows"] == 1
    assert report["files"]["users_interactions.csv"]["exact_duplicate_affected_rows"] == 2
    assert report["repeated_user_article_pairs"] == 1
    assert report["repeat_excess_interactions"] == 5
    assert report == profile(raw)


def test_changed_checksum_fails_without_writing_report(tmp_path, capsys):
    raw = sources(tmp_path)
    with (raw / "users_interactions.csv").open("a") as handle:
        handle.write("changed")
    output = tmp_path / "output"
    assert main(["profile", "--raw-dir", str(raw), "--output-dir", str(output)]) == 1
    assert "checksum" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    "change", ["header", "extra_field", "missing_field", "event", "id", "time", "quote"]
)
def test_malformed_source_even_with_matching_checksum(tmp_path, change):
    raw = sources(tmp_path)
    path = raw / "users_interactions.csv"
    with path.open(newline="") as handle:
        rows = list(csv.reader(handle))
    if change == "header":
        rows[0][0] = "wrong"
    elif change == "extra_field":
        rows[1].append("extra")
    elif change == "missing_field":
        rows[1].pop()
    else:
        column = {"event": 1, "id": 2, "time": 0, "quote": 0}[change]
        rows[1][column] = "invalid"
    with path.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    if change == "quote":
        with path.open("a") as handle:
            handle.write('"unterminated')
    refresh_manifest(raw)
    with pytest.raises(ProfileError):
        profile(raw)


def test_conflicting_equal_time_events_do_not_invent_order(tmp_path):
    raw = sources(
        tmp_path,
        articles=[(10, "CONTENT SHARED", "1"), (10, "CONTENT REMOVED", "1")],
        interactions=[(10, "1"), (11, "1")],
    )
    report = profile(raw)
    assert report["article_lifecycle"]["conflicting_same_time_content_ids"] == 1
    assert report["interaction_anomalies"]["ambiguous_previous_article_state"]["rows"] == 1
    assert report["interaction_anomalies"]["after_removal_before_reshare"]["rows"] == 0


def test_report_deterministic_and_sources_unchanged(tmp_path):
    raw = sources(tmp_path)
    before = {p.name: p.read_bytes() for p in raw.iterdir()}
    report = profile(raw)
    out = tmp_path / "output"
    write_report(report, out, raw)
    first = (out / "profile.json").read_bytes()
    write_report(profile(raw), out, raw)
    assert first == (out / "profile.json").read_bytes()
    assert before == {p.name: p.read_bytes() for p in raw.iterdir()}
    with pytest.raises(ProfileError, match="outside"):
        write_report(report, raw / "output", raw)


def test_manifest_cannot_redirect_sources(tmp_path):
    raw = sources(tmp_path)
    (raw / "manifest.json").write_text('{"files": [{"file": "../other.csv"}]}')
    with pytest.raises(ProfileError, match="exactly"):
        profile(raw)
