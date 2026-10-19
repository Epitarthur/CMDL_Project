"""Atomic, reproducible snapshot publication and separate holdout auditing."""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.types.json import Jsonb

from cmdl.data.ingest import (
    REASONS,
    _atomic_write,
    canonical_json,
    copy_rows,
    digest,
    validate_output,
)
from cmdl.data.temporal import (
    PERIODS,
    PROTOCOL_ID,
    ArticleAtCutoff,
    LifecycleEvent,
    Metadata,
    PublishedSnapshot,
    SourceInteraction,
    TemporalError,
    TrainingInteraction,
    _Timeline,
    partition,
    utc_boundary,
)

TRANSFORM_VERSION = "deskdrop-snapshot-v1"


def iso(value: datetime) -> str:
    return utc_boundary(value).isoformat().replace("+00:00", "Z")


def _inputs(connection, import_id: UUID, variant_id: str, cutoff: datetime):
    imported = connection.execute(
        """
        SELECT protocol_id, import_version, article_file_sha256, interaction_file_sha256
        FROM source_imports WHERE import_id=%s
    """,
        (import_id,),
    ).fetchone()
    if imported is None or imported[0] != PROTOCOL_ID:
        raise TemporalError("Select a completed import for the approved benchmark protocol")
    variant = connection.execute(
        "SELECT config_sha256,config FROM metadata_variants WHERE variant_id=%s", (variant_id,)
    ).fetchone()
    if variant is None:
        raise TemporalError("Unknown metadata variant")
    if variant[0] != digest(variant[1]):
        raise TemporalError("Metadata variant configuration checksum differs from stored config")
    events = [
        LifecycleEvent(*row)
        for row in connection.execute(
            """
        SELECT e.article_event_id,e.content_id,e.event_at,e.event_type,e.row_number,
            r.duplicate_row_number
        FROM article_events e JOIN source_rows r USING(file_sha256,row_number)
        WHERE e.file_sha256=%s ORDER BY e.content_id COLLATE "C",e.event_at,e.row_number
    """,
            (imported[2],),
        )
    ]
    metadata = _metadata(connection, imported[2], variant_id, cutoff)
    interactions = [
        SourceInteraction(*row)
        for row in connection.execute(
            """
        SELECT i.interaction_id,i.person_id,i.content_id,i.event_type,i.event_at,
            i.row_number,c.interaction_id
        FROM interactions i JOIN source_rows r USING(file_sha256,row_number)
        LEFT JOIN interactions c ON c.file_sha256=r.duplicate_file_sha256
            AND c.row_number=r.duplicate_row_number
        WHERE i.file_sha256=%s ORDER BY i.event_at,i.row_number
    """,
            (imported[3],),
        )
    ]
    return imported, variant[0], _Timeline(events, metadata), interactions


def _metadata(connection, article_hash, variant_id, cutoff=None):
    records = connection.execute(
        """
        SELECT v.metadata_version_id,v.content_id,v.share_event_id,e.event_at,
            v.title,v.text,v.url,v.lang,v.content_type,v.payload_sha256
        FROM metadata_versions v JOIN article_events e ON e.article_event_id=v.share_event_id
        WHERE e.file_sha256=%s AND v.variant_id=%s AND (%s::timestamptz IS NULL OR e.event_at < %s)
        ORDER BY e.event_at,e.row_number
    """,
        (article_hash, variant_id, cutoff, cutoff),
    )
    versions = {}
    for row in records:
        value = Metadata(*row[:3], utc_boundary(row[3]), *row[4:])
        payload = {
            field: getattr(value, field)
            for field in ("title", "text", "url", "lang", "content_type")
        }
        if digest(payload) != value.payload_sha256:
            raise TemporalError("Metadata payload checksum differs from stored fields")
        versions[row[2]] = value
    return versions


def _membership(timeline, interactions, cutoff):
    items = sorted(set(timeline.groups) | {i.content_id for i in interactions})
    articles = tuple(
        ArticleAtCutoff(
            item, timeline.availability(item, cutoff), timeline.metadata_at(item, cutoff)
        )
        for item in items
    )
    history = []
    partition_counts = Counter(
        dict.fromkeys(("source_duplicate", *REASONS, "canonical_eligible"), 0)
    )
    past = [i for i in interactions if i.event_at < cutoff]
    entries = []
    for event in past:
        reason = timeline.exclusion(event)
        entries.append((event, reason))
        partition_counts[
            "source_duplicate"
            if event.duplicate_of_interaction_id
            else reason or "canonical_eligible"
        ] += 1
        if not event.duplicate_of_interaction_id and reason is None:
            metadata = timeline.metadata_at(event.content_id, event.event_at)
            if metadata is None:
                raise TemporalError("Eligible history lacks strictly earlier metadata")
            history.append(
                TrainingInteraction(
                    event.interaction_id,
                    event.person_id,
                    event.content_id,
                    event.event_type,
                    event.event_at,
                    metadata,
                )
            )
    report = {
        "raw_history_rows": len(past),
        "history_partition": dict(partition_counts),
        "availability": dict(sorted(Counter(a.availability for a in articles).items())),
        "catalog_records": sum(a.availability == "available" for a in articles),
        "history_users": len({i.person_id for i in history}),
        "observed_consumed_pairs": len({(i.person_id, i.content_id) for i in past}),
    }
    return articles, entries, report


def _store_members(connection, snapshot_id, articles, entries):
    copy_rows(
        connection,
        "snapshot_articles",
        ["snapshot_id", "content_id", "metadata_version_id", "availability"],
        (
            (
                snapshot_id,
                a.content_id,
                a.metadata.metadata_version_id if a.metadata else None,
                a.availability,
            )
            for a in articles
        ),
    )
    copy_rows(
        connection,
        "snapshot_interactions",
        ["snapshot_id", "interaction_id", "duplicate_of_interaction_id", "exclusion_reason"],
        (
            (snapshot_id, i.interaction_id, i.duplicate_of_interaction_id, reason)
            for i, reason in entries
        ),
    )


def _publish(connection, import_id, variant_id, cutoff, inputs):
    imported, variant_sha, timeline, interactions = inputs
    descriptor = {
        "import_id": str(import_id),
        "import_version": imported[1],
        "protocol_id": PROTOCOL_ID,
        "files": [imported[2], imported[3]],
        "metadata_variant_id": variant_id,
        "metadata_config_sha256": variant_sha,
        "transform_version": TRANSFORM_VERSION,
        "as_of_at": iso(cutoff),
    }
    descriptor["metadata_manifest_sha256"] = digest(
        {
            str(event_id): {
                "metadata_version_id": str(version.metadata_version_id),
                "payload_sha256": version.payload_sha256,
            }
            for event_id, version in timeline.metadata.items()
            if version.shared_at < cutoff
        }
    )
    snapshot_id = uuid5(NAMESPACE_URL, "cmdl:snapshot:" + digest(descriptor))
    existing = connection.execute(
        "SELECT status,source_manifest FROM snapshots WHERE snapshot_id=%s", (snapshot_id,)
    ).fetchone()
    if existing:
        if existing[0] != "published" or existing[1].get("descriptor") != descriptor:
            raise TemporalError("Snapshot identity exists without compatible published inputs")
        return existing[1]["report"], False
    articles, entries, stats = _membership(timeline, interactions, cutoff)
    report = {"snapshot_id": str(snapshot_id), **descriptor, **stats}
    manifest = {"files": descriptor["files"], "descriptor": descriptor, "report": report}
    connection.execute(
        """
        INSERT INTO snapshots(snapshot_id,protocol_id,as_of_at,source_manifest,
            transform_version,metadata_variant_id)
        VALUES (%s,%s,%s,%s,%s,%s)
    """,
        (snapshot_id, PROTOCOL_ID, cutoff, Jsonb(manifest), TRANSFORM_VERSION, variant_id),
    )
    _store_members(connection, snapshot_id, articles, entries)
    connection.execute(
        "UPDATE snapshots SET status='published' WHERE snapshot_id=%s", (snapshot_id,)
    )
    return report, True


def publish_snapshot(
    connection: psycopg.Connection, import_id: UUID, cutoff: datetime, variant_id: str = "original"
) -> tuple[dict, bool]:
    """Publish one explicit cutoff. Official holdout preparation uses prepare_benchmark."""
    cutoff = utc_boundary(cutoff)
    with connection.transaction():
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        connection.execute("SET LOCAL lock_timeout='15s'")
        connection.execute("SELECT pg_advisory_xact_lock(68125240716303)")
        inputs = _inputs(connection, import_id, variant_id, cutoff)
        return _publish(connection, import_id, variant_id, cutoff, inputs)


def load_snapshot(connection: psycopg.Connection, snapshot_id: UUID) -> PublishedSnapshot:
    """Load only published, pre-cutoff data; callers never receive holdout interactions."""
    row = connection.execute(
        """
        SELECT protocol_id,as_of_at,metadata_variant_id,source_manifest,status,transform_version
        FROM snapshots WHERE snapshot_id=%s
    """,
        (snapshot_id,),
    ).fetchone()
    if row is None or row[4] != "published" or row[0] != PROTOCOL_ID or row[5] != TRANSFORM_VERSION:
        raise TemporalError("Select an explicit published snapshot for this protocol")
    protocol, cutoff, variant, manifest, _, _ = row
    cutoff = utc_boundary(cutoff)
    article_hash = manifest["files"][0]
    versions = _metadata(connection, article_hash, variant, cutoff)
    events = [
        LifecycleEvent(*r)
        for r in connection.execute(
            """
        SELECT e.article_event_id,e.content_id,e.event_at,e.event_type,e.row_number,
            r.duplicate_row_number
        FROM article_events e JOIN source_rows r USING(file_sha256,row_number)
        WHERE e.file_sha256=%s AND e.event_at < %s
            ORDER BY e.content_id COLLATE "C",e.event_at,e.row_number
    """,
            (article_hash, cutoff),
        )
    ]
    timeline = _Timeline(events, versions)
    by_version = {v.metadata_version_id: v for v in versions.values()}
    articles = tuple(
        ArticleAtCutoff(item, state, by_version[version] if version else None)
        for item, state, version in connection.execute(
            """
            SELECT content_id,availability,metadata_version_id FROM snapshot_articles
            WHERE snapshot_id=%s ORDER BY content_id COLLATE "C"
        """,
            (snapshot_id,),
        )
    )
    history, consumed = [], set()
    for person, content, event_type, at, event_id, duplicate, reason in connection.execute(
        """
        SELECT i.person_id,i.content_id,i.event_type,i.event_at,i.interaction_id,
            s.duplicate_of_interaction_id,s.exclusion_reason
        FROM snapshot_interactions s JOIN interactions i USING(interaction_id)
        WHERE s.snapshot_id=%s AND i.event_at < %s ORDER BY i.event_at,i.row_number
    """,
        (snapshot_id, cutoff),
    ):
        consumed.add((person, content))
        if duplicate is None and reason is None:
            at = utc_boundary(at)
            metadata = timeline.metadata_at(content, at)
            if metadata is None:
                raise TemporalError("Published history lacks strictly earlier metadata")
            history.append(TrainingInteraction(event_id, person, content, event_type, at, metadata))
    return PublishedSnapshot(
        snapshot_id, protocol, cutoff, variant, articles, tuple(history), frozenset(consumed)
    )


def _holdout(timeline, interactions, start, end):
    catalog = {
        item for item in timeline.groups if timeline.availability(item, start) == "available"
    }
    consumed = {(i.person_id, i.content_id) for i in interactions if i.event_at < start}
    partition_counts = Counter(
        dict.fromkeys(
            (
                "source_duplicate",
                *REASONS,
                "outside_fixed_catalog",
                "previously_consumed",
                "eligible_event",
            ),
            0,
        )
    )
    pairs, users = set(), set()
    examples = {}
    for event in interactions:
        if not start <= event.event_at < end:
            continue
        users.add(event.person_id)
        reason = timeline.exclusion(event)
        category = (
            "source_duplicate"
            if event.duplicate_of_interaction_id
            else reason
            or ("outside_fixed_catalog" if event.content_id not in catalog else None)
            or (
                "previously_consumed"
                if (event.person_id, event.content_id) in consumed
                else "eligible_event"
            )
        )
        partition_counts[category] += 1
        if category == "eligible_event":
            pairs.add((event.person_id, event.content_id))
        elif len(examples.setdefault(category, [])) < 5:
            examples[category].append(
                {"row_number": event.row_number, "content_id": event.content_id}
            )
    positive_users = {user for user, _ in pairs}
    return {
        "start_inclusive": iso(start),
        "end_exclusive": iso(end),
        "event_partition": dict(partition_counts),
        "distinct_relevant_pairs": len(pairs),
        "holdout_users": len(users),
        "users_with_eligible_positives": len(positive_users),
        "users_without_eligible_positives": len(users - positive_users),
        "exclusion_examples": examples,
    }


def prepare_benchmark(
    connection: psycopg.Connection, import_id: UUID, variant_id="original"
) -> tuple[dict, bool]:
    """Publish both fitting snapshots atomically; holdout audits are evaluation-only."""
    with connection.transaction():
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        connection.execute("SET LOCAL lock_timeout='15s'")
        connection.execute("SELECT pg_advisory_xact_lock(68125240716303)")
        inputs = _inputs(
            connection, import_id, variant_id, max(start for start, _ in PERIODS.values())
        )
        _, _, timeline, interactions = inputs
        reports, created, holdouts = {}, False, {}
        for name, (start, end) in PERIODS.items():
            reports[name], new = _publish(connection, import_id, variant_id, start, inputs)
            created |= new
            holdouts[name] = _holdout(timeline, interactions, start, end)
        counts = Counter(dict.fromkeys(("train", "validation", "test", "after_test"), 0))
        counts.update(partition(i.event_at) for i in interactions)
        return {
            "report_version": 1,
            "protocol_id": PROTOCOL_ID,
            "transform_version": TRANSFORM_VERSION,
            "import_id": str(import_id),
            "metadata_variant_id": variant_id,
            "raw_event_partitions": dict(counts),
            "snapshots": reports,
            "holdouts": holdouts,
            "notes": [
                "Holdout audits are evaluation-only and never returned by the model "
                "snapshot loader.",
                "Every candidate uses one fixed request-time catalog and all strictly "
                "prior consumption.",
                "Canonical modeling history excludes invalid lifecycle events and attaches "
                "earlier metadata.",
                "Counts precede T10 minimum-history/cohort filters; no ranking metrics "
                "are claimed.",
            ],
        }, created


def write_benchmark_report(report: dict, output_dir: Path):
    validate_output(output_dir, Path("data/raw"))
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Deskdrop snapshots and temporal partitions",
        "",
        f"Import: `{report['import_id']}`",
        "",
        "| Request | Snapshot | Available catalog | Eligible fitting events |",
        "| --- | --- | ---: | ---: |",
    ]
    for name, snapshot in report["snapshots"].items():
        lines.append(
            f"| {name} | `{snapshot['snapshot_id']}` | {snapshot['catalog_records']:,} | "
            f"{snapshot['history_partition']['canonical_eligible']:,} |"
        )
    lines += [
        "",
        "Raw half-open interval counts: " + canonical_json(report["raw_event_partitions"]),
        "",
    ]
    for name, holdout in report["holdouts"].items():
        lines += [f"## {name.title()} holdout audit", "", "| Category | Events |", "| --- | ---: |"]
        lines += [
            f"| {category} | {count:,} |" for category, count in holdout["event_partition"].items()
        ]
        lines += [
            "",
            f"Distinct eligible user/article pairs: {holdout['distinct_relevant_pairs']:,}.",
            f"Users with / without eligible positives: "
            f"{holdout['users_with_eligible_positives']:,} / "
            f"{holdout['users_without_eligible_positives']:,}.",
            "",
        ]
    lines += [f"- {note}" for note in report["notes"]]
    _atomic_write(
        output_dir / "benchmark.json", json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write(output_dir / "benchmark.md", "\n".join(lines) + "\n")
