"""Transactional Deskdrop ingestion. No raw events are deleted or modeled here."""

import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from cmdl.data.profile import SCHEMAS, load_sources

IMPORT_VERSION = "deskdrop-import-v1"
PROTOCOL_ID = "deskdrop-temporal-v1"
METADATA_FIELDS = {
    "title": "title",
    "text": "text",
    "url": "url",
    "lang": "lang",
    "content_type": "contentType",
}
ORIGINAL_CONFIG = {"kind": "original", "fields": METADATA_FIELDS}
REASONS = ("unresolved", "removal_only", "pre_share", "ambiguous", "removed")


class IngestError(ValueError):
    """Validated sources conflict with existing normalized provenance."""


def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def identity(kind: str, source_hash: str, row_number: int):
    return uuid5(NAMESPACE_URL, f"cmdl:{kind}:{source_hash}:{row_number}")


def copy_rows(connection, table: str, columns: list[str], records) -> None:
    query = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns))
    )
    with connection.cursor().copy(query) as stream:
        for record in records:
            stream.write_row(record)


def _raw_records(rows, source_hash, columns):
    canonical = {}
    for number, row in enumerate(rows, 1):
        key = tuple(row[column] for column in columns)
        previous = canonical.get(key)
        canonical.setdefault(key, number)
        yield source_hash, number, Jsonb(row), source_hash if previous else None, previous


def _ensure_original(connection):
    expected = digest(ORIGINAL_CONFIG)
    existing = connection.execute(
        "SELECT variant_id, config, config_sha256 FROM metadata_variants WHERE kind='original'"
    ).fetchone()
    if existing:
        if existing != ("original", ORIGINAL_CONFIG, expected):
            raise IngestError("Original metadata definition conflicts with the importer")
        return
    connection.execute(
        "INSERT INTO metadata_variants(variant_id,kind,config,config_sha256) "
        "VALUES ('original','original',%s,%s)",
        (Jsonb(ORIGINAL_CONFIG), expected),
    )


def _import_file(connection, name, rows, stats, manifest):
    source_hash = stats["sha256"]
    category = "article" if name == "shared_articles.csv" else "interaction"
    provenance = (
        name,
        stats["bytes"],
        manifest["source_url"],
        str(manifest["upstream_version"]),
        category,
    )
    existing = connection.execute(
        "SELECT filename, bytes, dataset_ref, upstream_version, event_category "
        "FROM source_files WHERE sha256=%s FOR UPDATE",
        (source_hash,),
    ).fetchone()
    if existing:
        if existing != provenance:
            raise IngestError(f"{name}: stored file provenance conflicts with the manifest")
        completed = connection.execute(
            "SELECT 1 FROM source_imports WHERE article_file_sha256=%s OR "
            "interaction_file_sha256=%s LIMIT 1",
            (source_hash, source_hash),
        ).fetchone()
        if not completed:
            raise IngestError(
                f"{name}: existing file has no completed import; inspect the database"
            )
        return
    connection.execute(
        "INSERT INTO "
        "source_files(sha256,filename,bytes,dataset_ref,upstream_version,event_"
        "category) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (source_hash, *provenance),
    )
    copy_rows(
        connection,
        "source_rows",
        [
            "file_sha256",
            "row_number",
            "raw_fields",
            "duplicate_file_sha256",
            "duplicate_row_number",
        ],
        _raw_records(rows, source_hash, SCHEMAS[name][0]),
    )
    if category == "article":
        copy_rows(
            connection,
            "article_events",
            [
                "article_event_id",
                "content_id",
                "event_type",
                "event_at",
                "file_sha256",
                "row_number",
            ],
            (
                (
                    identity("article-event", source_hash, n),
                    row["contentId"],
                    row["eventType"],
                    datetime.fromtimestamp(int(row["timestamp"]), UTC),
                    source_hash,
                    n,
                )
                for n, row in enumerate(rows, 1)
            ),
        )

        def versions():
            for n, row in enumerate(rows, 1):
                if row["eventType"] != "CONTENT SHARED":
                    continue
                payload = {field: row[column] for field, column in METADATA_FIELDS.items()}
                yield (
                    identity("original-metadata", source_hash, n),
                    row["contentId"],
                    identity("article-event", source_hash, n),
                    "original",
                    *(payload[field] for field in METADATA_FIELDS),
                    digest(payload),
                )

        copy_rows(
            connection,
            "metadata_versions",
            [
                "metadata_version_id",
                "content_id",
                "share_event_id",
                "variant_id",
                *METADATA_FIELDS,
                "payload_sha256",
            ],
            versions(),
        )
    else:
        copy_rows(
            connection,
            "interactions",
            [
                "interaction_id",
                "person_id",
                "content_id",
                "session_id",
                "event_type",
                "event_at",
                "file_sha256",
                "row_number",
            ],
            (
                (
                    identity("interaction", source_hash, n),
                    row["personId"],
                    row["contentId"],
                    row["sessionId"],
                    row["eventType"],
                    datetime.fromtimestamp(int(row["timestamp"]), UTC),
                    source_hash,
                    n,
                )
                for n, row in enumerate(rows, 1)
            ),
        )


def _report(connection, import_id, rows, stats):
    article_hash = stats["shared_articles.csv"]["sha256"]
    interaction_hash = stats["users_interactions.csv"]["sha256"]
    source_manifest = {"files": [article_hash, interaction_hash]}
    temporal = Counter(dict.fromkeys((*REASONS, "no_temporal_exclusion"), 0))
    partition = Counter(dict.fromkeys(("source_duplicate", *REASONS, "canonical_eligible"), 0))
    audit = []
    query = """
        SELECT i.row_number, r.duplicate_row_number, interaction_exclusion(i, %s)
        FROM interactions i JOIN source_rows r USING (file_sha256, row_number)
        WHERE i.file_sha256=%s ORDER BY i.row_number
    """
    for number, duplicate, reason in connection.execute(
        query, (Jsonb(source_manifest), interaction_hash)
    ):
        temporal[reason or "no_temporal_exclusion"] += 1
        partition["source_duplicate" if duplicate else reason or "canonical_eligible"] += 1
        if duplicate or reason:
            audit.append(
                {
                    "file_sha256": interaction_hash,
                    "row_number": number,
                    "duplicate_of_row_number": duplicate,
                    "exclusion_reason": reason,
                }
            )
    counts = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM source_rows WHERE file_sha256 IN (%s,%s)),
            (SELECT count(*) FROM article_events WHERE file_sha256=%s),
            (SELECT count(*) FROM metadata_versions v JOIN article_events e
                ON e.article_event_id=v.share_event_id
                WHERE e.file_sha256=%s AND v.variant_id='original'),
            (SELECT count(*) FROM interactions WHERE file_sha256=%s)
    """,
        (article_hash, interaction_hash, article_hash, article_hash, interaction_hash),
    ).fetchone()
    articles, interactions = rows["shared_articles.csv"], rows["users_interactions.csv"]
    shares = sum(row["eventType"] == "CONTENT SHARED" for row in articles)
    expected = (len(articles) + len(interactions), len(articles), shares, len(interactions))
    if counts != expected or sum(partition.values()) != len(interactions):
        raise IngestError("Database import totals do not reconcile with the validated sources")
    authors = {row["authorPersonId"] for row in articles}
    readers = {row["personId"] for row in interactions}
    shared_ids = {row["contentId"] for row in articles if row["eventType"] == "CONTENT SHARED"}
    event_ids = {row["contentId"] for row in articles}
    return {
        "report_version": 1,
        "import_id": str(import_id),
        "import_version": IMPORT_VERSION,
        "protocol_id": PROTOCOL_ID,
        "sources": {
            name: {
                key: stats[name][key]
                for key in ("sha256", "bytes", "rows", "exact_duplicate_excess_rows")
            }
            for name in SCHEMAS
        },
        "stored": {
            "source_rows": counts[0],
            "article_events": counts[1],
            "original_metadata_versions": counts[2],
            "interactions": counts[3],
        },
        "identities": {
            "authors": len(authors),
            "interacting_users": len(readers),
            "user_union": len(authors | readers),
            "article_union": len(event_ids | {row["contentId"] for row in interactions}),
            "removal_only_articles": len(event_ids - shared_ids),
            "interaction_only_articles": len(
                {row["contentId"] for row in interactions} - event_ids
            ),
        },
        "interaction_temporal_counts": dict(temporal),
        "interaction_partition": dict(partition),
        "audit_rows": audit,
        "notes": [
            "All raw events and original CSV strings are retained; exclusions do not delete rows.",
            "Partition removes exact source copies first, then uses benchmark "
            "exclusion precedence.",
            "Temporal counts include duplicate copies and must not be added to the partition.",
            "Eligibility here precedes cutoff, minimum-history and request-time "
            "candidate filters in T08/T10.",
            "Audit rows retain both duplicate lineage and temporal exclusion when they overlap.",
        ],
    }


def ingest(connection: psycopg.Connection, raw_dir: Path) -> tuple[dict, bool]:
    """Validate first, then atomically import both files and their durable audit.

    Returns (report, newly_imported). Replays return the exact stored report. Shared
    files from a completed prior import can be reused, even after snapshot sealing.
    """
    manifest, rows, stats = load_sources(raw_dir)
    if not isinstance(manifest.get("source_url"), str) or not manifest["source_url"].strip():
        raise IngestError("Manifest source_url is required for stored provenance")
    version = manifest.get("upstream_version")
    if type(version) not in (str, int) or not str(version).strip():
        raise IngestError("Manifest upstream_version is required for stored provenance")
    article_hash = stats["shared_articles.csv"]["sha256"]
    interaction_hash = stats["users_interactions.csv"]["sha256"]
    import_id = uuid5(
        NAMESPACE_URL, f"cmdl:{IMPORT_VERSION}:{PROTOCOL_ID}:{article_hash}:{interaction_hash}"
    )
    with connection.transaction():
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        connection.execute("SET LOCAL lock_timeout='15s'")
        connection.execute("SELECT pg_advisory_xact_lock(68125240716302)")
        if connection.execute("SELECT to_regclass('source_imports')").fetchone()[0] is None:
            raise IngestError("Run cmdl migrate before importing sources")
        existing = connection.execute(
            "SELECT report FROM source_imports WHERE import_id=%s", (import_id,)
        ).fetchone()
        if existing:
            for name in SCHEMAS:
                stored = connection.execute(
                    "SELECT dataset_ref, upstream_version FROM source_files WHERE sha256=%s",
                    (stats[name]["sha256"],),
                ).fetchone()
                if stored != (manifest["source_url"], str(version)):
                    raise IngestError(f"{name}: stored provenance differs from the manifest")
            return existing[0], False
        _ensure_original(connection)
        # Only identities use ON CONFLICT; immutable source rows/events are never reinserted.
        people = {row["authorPersonId"] for row in rows["shared_articles.csv"]} | {
            row["personId"] for row in rows["users_interactions.csv"]
        }
        articles = {row["contentId"] for file_rows in rows.values() for row in file_rows}
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO users(person_id) VALUES (%s) ON CONFLICT DO NOTHING",
                [(person,) for person in sorted(people)],
            )
            cursor.executemany(
                "INSERT INTO articles(content_id) VALUES (%s) ON CONFLICT DO NOTHING",
                [(content,) for content in sorted(articles)],
            )
        for name in SCHEMAS:
            _import_file(connection, name, rows[name], stats[name], manifest)
        report = _report(connection, import_id, rows, stats)
        connection.execute(
            """
            INSERT INTO source_imports(import_id,import_version,protocol_id,article_file_sha256,
                interaction_file_sha256,source_manifest,report) VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
            (
                import_id,
                IMPORT_VERSION,
                PROTOCOL_ID,
                article_hash,
                interaction_hash,
                Jsonb(manifest),
                Jsonb(report),
            ),
        )
    return report, True


def validate_output(output_dir: Path, raw_dir: Path):
    if output_dir.resolve().is_relative_to(raw_dir.resolve()):
        raise IngestError("Output directory must be outside raw-dir")


def _atomic_write(path: Path, text: str):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temp = Path(handle.name)
        try:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def write_import_report(report: dict, output_dir: Path, raw_dir: Path):
    validate_output(output_dir, raw_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Deskdrop import audit",
        "",
        f"Import: `{report['import_id']}`",
        "",
        "All source rows are retained. This partition describes modeling "
        "exclusions before temporal splits.",
        "",
        "| Interaction category | Rows |",
        "| --- | ---: |",
    ]
    lines += [f"| {key} | {count:,} |" for key, count in report["interaction_partition"].items()]
    lines += ["", "Stored totals: " + json.dumps(report["stored"], sort_keys=True), ""]
    lines += [f"- {note}" for note in report["notes"]]
    _atomic_write(output_dir / "import.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    _atomic_write(output_dir / "import.md", "\n".join(lines) + "\n")


def write_failure(output_dir: Path, raw_dir: Path, stage: str, message: str):
    validate_output(output_dir, raw_dir)
    folder = output_dir / "failures"
    folder.mkdir(parents=True, exist_ok=True)
    value = {
        "stage": stage,
        "message": message,
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    _atomic_write(
        folder / f"{uuid5(NAMESPACE_URL, value['occurred_at'])}.json",
        json.dumps(value, indent=2) + "\n",
    )
