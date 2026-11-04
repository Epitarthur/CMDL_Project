"Explicit training and atomic publication; no work is fabricated at API startup."

import hashlib
import importlib.metadata
import json
import os
import pickle
import platform
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cmdl.data.snapshots import load_snapshot, publish_snapshot
from cmdl.quality.store import check_all
from cmdl.recommender.models import PROTOCOL, Ranker, records_from_snapshot


def code_fingerprint():
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def publish(connection, ranker, output_dir, source_manifest):
    ranker.prepare()
    payload = pickle.dumps(ranker, protocol=5)
    checksum = hashlib.sha256(payload).hexdigest()
    release = str(uuid5(NAMESPACE_URL, f"cmdl-model-v1:{checksum}"))
    folder = Path(output_dir).resolve() / release
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "model.pkl"
    temporary = folder / "model.tmp"
    temporary.write_bytes(payload)
    os.replace(temporary, target)
    manifest = {
        "model_version": "cmdl-ranker-v1",
        "release_id": release,
        "snapshot_id": ranker.snapshot_id,
        "cutoff": ranker.cutoff,
        "protocol": PROTOCOL,
        "seed": ranker.seed,
        "alpha": ranker.alpha,
        "semantic": ranker.semantic_manifest,
        "source_manifest": source_manifest,
        "code_sha256": code_fingerprint(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": sorted(
            f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
        ),
        "quality_version": "metadata-rules-v1",
        "support_counts": ranker.support_counts,
        "record_versions": {
            item: str(record["metadata_version_id"]) for item, record in ranker.catalog.items()
        },
        "artifact_sha256": checksum,
        "candidate_count": len(ranker.ids),
        "cached_users": len(ranker.cache),
        "minimum_history": "iterative 5-distinct-partner user/item core; training only",
        "demo_only": True,
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    connection.execute(
        "INSERT INTO serving_releases VALUES (%s,%s,%s,%s,%s,DEFAULT) ON CONFLICT DO NOTHING",
        (release, ranker.snapshot_id, str(target), checksum, Jsonb(manifest)),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO serving_candidates VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            [
                (release, person, Jsonb([i["content_id"] for i in items]))
                for person, items in ranker.cache.items()
            ],
        )
    connection.execute(
        (
            "INSERT INTO app_settings VALUES ('active_release',%s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        ),
        (release,),
    )
    return manifest


def train_demo(
    import_id=None, *, semantic=False, alpha=0.5, seed=42, output_dir="artifacts/models"
):
    with psycopg.connect() as connection:
        if import_id is None:
            rows = connection.execute(
                "SELECT import_id FROM source_imports ORDER BY import_id"
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    "Specify --import-id when there is not exactly one completed import"
                )
            import_id = rows[0][0]
        else:
            import_id = UUID(str(import_id))
        # The export ends in February 2017. This is a demo catalog, never a test input.
        published, _ = publish_snapshot(connection, import_id, datetime(2017, 3, 1, tzinfo=UTC))
        snapshot_id = UUID(published["snapshot_id"])
        check_all(connection)
        snapshot = load_snapshot(connection, snapshot_id)
        records = records_from_snapshot(snapshot)
        with connection.cursor(row_factory=dict_row) as cursor:
            corrections = cursor.execute(
                "SELECT m.* FROM live_metadata_heads h JOIN "
                "metadata_versions m USING(metadata_version_id)"
            ).fetchall()
        for correction in corrections:
            for article in snapshot.candidates():
                if article.content_id == correction["content_id"]:
                    record = {
                        key: str(value) if isinstance(value, (UUID, datetime)) else value
                        for key, value in correction.items()
                    }
                    records[str(article.metadata.metadata_version_id)] = record
        manifest = connection.execute(
            "SELECT source_manifest FROM snapshots WHERE snapshot_id=%s", (snapshot_id,)
        ).fetchone()[0]
    ranker = Ranker(snapshot, seed=seed, alpha=alpha, semantic=semantic, records=records)
    with psycopg.connect() as connection:
        return publish(connection, ranker, output_dir, manifest)
