"""Verify the tracked dataset evidence and the independent upstream comparison command."""

import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from cmdl.data.profile import profile

ROOT = Path(__file__).resolve().parents[2]


def test_committed_source_reconciliation():
    result = profile(ROOT / "data/raw")
    assert result["provenance"]["upstream_version"] == 5
    assert result["join_reconciliation"] == {
        "raw_article_event_inner_join_rows": 72680,
        "unique_shared_metadata_inner_join_rows": 72269,
        "distinct_user_article_pairs": 40710,
        "temporal_or_join_flagged_rows": 166,
    }
    assert result["shared_metadata"] == {
        "rows": 3047,
        "languages": {"en": 2211, "pt": 829, "es": 2, "la": 3, "ja": 2},
        "distinct_urls": 3023,
        "distinct_titles": 3003,
        "missing_core_fields": dict.fromkeys(["title", "text", "url", "lang"], 0),
    }
    assert result["article_lifecycle"]["latest_unambiguous_shared_content_ids"] == 2983
    assert result["interaction_anomaly_combinations"] == {
        "after_removal_before_reshare": 16,
        "after_removal_before_reshare + removal_only_content_id": 3,
        "before_first_share": 95,
        "no_temporal_or_join_flag": 72146,
        "removal_only_content_id": 40,
        "same_timestamp_as_article_event": 12,
    }


@pytest.mark.parametrize("case", ["match", "changed_archive", "changed_license", "wrong_dataset"])
def test_upstream_verification_cli(tmp_path, case):
    raw = tmp_path / "raw"
    raw.mkdir()
    payload = b"original source bytes"
    import hashlib

    (raw / "source.csv").write_bytes(payload)
    (raw / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "file": "source.csv",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                    }
                ]
            }
        )
    )
    metadata = {
        "ref": "gspmoreira/articles-sharing-reading-from-cit-deskdrop",
        "licenseName": "Database: Open Database, Contents: Database Contents",
        "versions": [{"versionNumber": 5}],
    }
    if case == "changed_license":
        metadata["licenseName"] = "different terms"
    if case == "wrong_dataset":
        metadata["ref"] = "another/dataset"
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    archive_path = tmp_path / "source.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("source.csv", b"changed bytes" if case == "changed_archive" else payload)
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_upstream.py"),
            str(metadata_path),
            str(archive_path),
            "--raw-dir",
            str(raw),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == (0 if case == "match" else 1)
    if case != "match":
        assert "Upstream verification failed" in run.stderr
