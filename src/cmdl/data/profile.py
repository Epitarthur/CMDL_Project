"""Deterministic, read-only Deskdrop source verification. All IDs remain strings."""

import csv
import hashlib
import io
import json
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ARTICLE_COLUMNS = (
    "timestamp,eventType,contentId,authorPersonId,authorSessionId,authorUserAgent,"
    "authorRegion,authorCountry,contentType,url,title,text,lang"
).split(",")
INTERACTION_COLUMNS = (
    "timestamp,eventType,contentId,personId,sessionId,userAgent,userRegion,userCountry"
).split(",")
SCHEMAS = {
    "shared_articles.csv": (ARTICLE_COLUMNS, {"CONTENT SHARED", "CONTENT REMOVED"}),
    "users_interactions.csv": (
        INTERACTION_COLUMNS,
        {"VIEW", "LIKE", "BOOKMARK", "FOLLOW", "COMMENT CREATED"},
    ),
}
ID_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


class ProfileError(ValueError):
    """A source does not meet its byte-level or structural contract."""


def utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def read_source(path: Path, expected: dict) -> tuple[list[dict], dict]:
    columns, events = SCHEMAS[path.name]
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected["sha256"] or len(payload) != expected["bytes"]:
        raise ProfileError(f"{path.name}: checksum or byte count differs from manifest")
    rows = []
    try:
        with io.StringIO(payload.decode("utf-8"), newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames != columns:
                raise ProfileError(f"{path.name}: unexpected CSV header {reader.fieldnames!r}")
            for row_number, row in enumerate(reader, 1):
                where = f"{path.name}: data row {row_number}"
                if None in row or any(value is None for value in row.values()):
                    raise ProfileError(f"{where}: wrong number of fields")
                if any("\x00" in value for value in row.values()):
                    raise ProfileError(f"{where}: NUL characters cannot be stored as text")
                if row["eventType"] not in events:
                    raise ProfileError(f"{where}: unknown eventType {row['eventType']!r}")
                for column in columns:
                    if column.endswith("Id") and not ID_PATTERN.fullmatch(row[column]):
                        raise ProfileError(f"{where}: invalid string identifier in {column}")
                if not re.fullmatch(r"[0-9]+", row["timestamp"]):
                    raise ProfileError(f"{where}: invalid Unix timestamp")
                try:
                    utc(int(row["timestamp"]))
                except (ValueError, OverflowError, OSError) as exc:
                    raise ProfileError(f"{where}: timestamp outside supported range") from exc
                rows.append(row)
    except (csv.Error, UnicodeError) as exc:
        raise ProfileError(f"{path.name}: malformed CSV: {exc}") from exc
    if not rows:
        raise ProfileError(f"{path.name}: empty dataset")
    timestamps = [int(row["timestamp"]) for row in rows]
    duplicates = Counter(tuple(row[column] for column in columns) for row in rows)
    summary = {
        "sha256": digest,
        "bytes": len(payload),
        "rows": len(rows),
        "columns": columns,
        "grain": (
            "article lifecycle event row; contentId can repeat"
            if path.name == "shared_articles.csv"
            else "user interaction event row; user/article pairs can repeat"
        ),
        "distinct_ids": {
            column: len({row[column] for row in rows})
            for column in columns
            if column.endswith("Id")
        },
        "timestamp_min_utc": utc(min(timestamps)),
        "timestamp_max_utc": utc(max(timestamps)),
        "events": dict(sorted(Counter(row["eventType"] for row in rows).items())),
        "missing_fields": {
            column: {
                "count": sum(not row[column].strip() for row in rows),
                "rate": sum(not row[column].strip() for row in rows) / len(rows),
            }
            for column in columns
        },
        "exact_duplicate_excess_rows": sum(n - 1 for n in duplicates.values()),
        "exact_duplicate_affected_rows": sum(n for n in duplicates.values() if n > 1),
        "monthly_events_utc": dict(sorted(Counter(utc(t)[:7] for t in timestamps).items())),
    }
    if "lang" in columns:
        summary["languages"] = dict(sorted(Counter(row["lang"] for row in rows).items()))
    return rows, summary


def load_sources(raw_dir: Path) -> tuple[dict, dict[str, list[dict]], dict[str, dict]]:
    """Validate both files; parse the exact in-memory bytes whose hashes were checked."""
    try:
        manifest = json.loads((raw_dir / "manifest.json").read_text())
        entries = {entry["file"]: entry for entry in manifest["files"]}
        if len(manifest["files"]) != len(SCHEMAS) or set(entries) != set(SCHEMAS):
            raise ProfileError("manifest must list exactly the two Deskdrop CSVs")
        for entry in entries.values():
            if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ProfileError("manifest contains an invalid SHA-256")
            if type(entry["bytes"]) is not int or entry["bytes"] < 0:
                raise ProfileError("manifest contains an invalid byte count")
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileError(f"Invalid manifest: {exc}") from exc
    article_rows, articles = read_source(
        raw_dir / "shared_articles.csv", entries["shared_articles.csv"]
    )
    interaction_rows, interactions = read_source(
        raw_dir / "users_interactions.csv", entries["users_interactions.csv"]
    )
    return (
        manifest,
        {
            "shared_articles.csv": article_rows,
            "users_interactions.csv": interaction_rows,
        },
        {"shared_articles.csv": articles, "users_interactions.csv": interactions},
    )


def profile(raw_dir: Path) -> dict:
    """Verify the manifest and calculate source-level counts without excluding rows."""
    manifest, rows, stats = load_sources(raw_dir)
    article_rows, interaction_rows = rows["shared_articles.csv"], rows["users_interactions.csv"]
    articles, interactions = stats["shared_articles.csv"], stats["users_interactions.csv"]
    timelines = defaultdict(list)
    for row in article_rows:
        timelines[row["contentId"]].append((int(row["timestamp"]), row["eventType"]))
    for events in timelines.values():
        events.sort()
    first_share = {
        key: min(t for t, event in events if event == "CONTENT SHARED")
        for key, events in timelines.items()
        if any(e == "CONTENT SHARED" for _, e in events)
    }
    anomalies = Counter()
    anomaly_ids = defaultdict(set)
    combinations = Counter()
    examples = defaultdict(list)
    # Evaluate strictly earlier events; equal-time cases are measured separately.
    timeline_times = {key: [t for t, _ in events] for key, events in timelines.items()}
    for index, row in enumerate(interaction_rows, 1):
        key, timestamp = row["contentId"], int(row["timestamp"])
        flags = []
        if key not in timelines:
            flags.append("unresolved_content_id")
        elif key not in first_share:
            flags.append("removal_only_content_id")
        elif timestamp < first_share[key]:
            flags.append("before_first_share")
        if key in timelines:
            position = bisect_right(timeline_times[key], timestamp - 1)
            equal_end = bisect_right(timeline_times[key], timestamp)
            if equal_end > position:
                flags.append("same_timestamp_as_article_event")
            if position:
                last_time = timeline_times[key][position - 1]
                last_events = {e for t, e in timelines[key] if t == last_time}
                if last_events == {"CONTENT REMOVED"}:
                    flags.append("after_removal_before_reshare")
                elif len(last_events) > 1:
                    flags.append("ambiguous_previous_article_state")
        for flag in flags:
            anomalies[flag] += 1
            anomaly_ids[flag].add(key)
            if len(examples[flag]) < 5:
                examples[flag].append(
                    {"data_row": index, "content_id": key, "timestamp_utc": utc(timestamp)}
                )
        combinations[" + ".join(sorted(flags)) or "no_temporal_or_join_flag"] += 1
    all_flags = [
        "unresolved_content_id",
        "removal_only_content_id",
        "before_first_share",
        "same_timestamp_as_article_event",
        "after_removal_before_reshare",
        "ambiguous_previous_article_state",
    ]
    repeated = Counter((r["personId"], r["contentId"]) for r in interaction_rows)
    shares = [row for row in article_rows if row["eventType"] == "CONTENT SHARED"]
    article_event_counts = Counter(row["contentId"] for row in article_rows)
    return {
        "profile_version": 2,
        "source": manifest.get("source"),
        "source_url": manifest.get("source_url"),
        "provenance": {
            "upstream_version": manifest.get("upstream_version"),
            "license": manifest.get("license"),
            "verification_evidence": manifest.get("verification_evidence"),
        },
        "files": {"shared_articles.csv": articles, "users_interactions.csv": interactions},
        "shared_metadata": {
            "rows": len(shares),
            "languages": dict(sorted(Counter(row["lang"] for row in shares).items())),
            "distinct_urls": len({row["url"] for row in shares}),
            "distinct_titles": len({row["title"] for row in shares}),
            "missing_core_fields": {
                field: sum(not row[field].strip() for row in shares)
                for field in ["title", "text", "url", "lang"]
            },
        },
        "join_reconciliation": {
            "raw_article_event_inner_join_rows": sum(
                article_event_counts[row["contentId"]] for row in interaction_rows
            ),
            "unique_shared_metadata_inner_join_rows": sum(
                row["contentId"] in first_share for row in interaction_rows
            ),
            "distinct_user_article_pairs": len(repeated),
            "temporal_or_join_flagged_rows": len(interaction_rows)
            - combinations["no_temporal_or_join_flag"],
        },
        "article_lifecycle": {
            "shared_content_ids": len(first_share),
            "latest_unambiguous_shared_content_ids": sum(
                {e for t, e in events if t == events[-1][0]} == {"CONTENT SHARED"}
                for events in timelines.values()
            ),
            "removal_only_content_ids": len(set(timelines) - set(first_share)),
            "multiple_share_content_ids": sum(
                sum(e == "CONTENT SHARED" for _, e in events) > 1 for events in timelines.values()
            ),
            "share_and_removal_content_ids": sum(
                len({e for _, e in events}) > 1 for events in timelines.values()
            ),
            "conflicting_same_time_content_ids": sum(
                any(len({e for t2, e in events if t2 == t}) > 1 for t, _ in events)
                for events in timelines.values()
            ),
        },
        "interaction_anomalies": {
            flag: {
                "rows": anomalies[flag],
                "rate": anomalies[flag] / len(interaction_rows),
                "content_ids": len(anomaly_ids[flag]),
                "examples": examples[flag],
            }
            for flag in all_flags
        },
        "interaction_anomaly_combinations": dict(sorted(combinations.items())),
        "repeated_user_article_pairs": sum(n > 1 for n in repeated.values()),
        "repeat_excess_interactions": sum(n - 1 for n in repeated.values()),
        "notes": [
            "Raw files are unchanged. No records are excluded by this profiler.",
            "IDs are exact CSV strings; no numeric or floating point conversion is performed.",
            "Missing means empty or whitespace-only. "
            "Duplicate excess counts copies after the first.",
            "Anomaly groups overlap; combination counts partition all interaction rows.",
            "Temporal state uses strictly earlier article events; "
            "equal timestamps are flagged separately.",
            "Repeated shares are not necessarily re-shares after removal.",
            "Publisher version/license evidence and slide reconciliation are documented in docs; "
            "the profiler verifies source bytes and does not re-query upstream services.",
        ],
    }


def markdown(report: dict) -> str:
    lines = ["# Deskdrop source profile", "", "Reproduce: `uv run cmdl profile`", ""]
    for name, stats in report["files"].items():
        lines += [
            f"## {name}",
            "",
            f"- Rows: {stats['rows']:,}",
            f"- Distinct IDs: {stats['distinct_ids']}",
            f"- UTC range: {stats['timestamp_min_utc']} to {stats['timestamp_max_utc']}",
            f"- Events: {stats['events']}",
            f"- Exact duplicate excess rows: {stats['exact_duplicate_excess_rows']}",
            "",
            "| Field | Missing | Rate |",
            "| --- | ---: | ---: |",
        ]
        lines += [
            f"| {field} | {value['count']} | {value['rate']:.2%} |"
            for field, value in stats["missing_fields"].items()
        ]
        lines += ["", "Monthly UTC event counts: " + json.dumps(stats["monthly_events_utc"]), ""]
    lines += [
        "## Join and temporal anomalies",
        "",
        "Groups overlap. The JSON report includes examples and mutually exclusive combinations.",
        "",
        "| Flag | Rows | Rate | Content IDs |",
        "| --- | ---: | ---: | ---: |",
    ]
    for flag, value in report["interaction_anomalies"].items():
        lines.append(f"| {flag} | {value['rows']} | {value['rate']:.2%} | {value['content_ids']} |")
    lines += [
        "",
        "Article lifecycle: " + json.dumps(report["article_lifecycle"], sort_keys=True),
        "",
        f"Repeated user/article pairs: {report['repeated_user_article_pairs']:,}; "
        f"interactions beyond the first: {report['repeat_excess_interactions']:,}.",
        "",
        "## Interpretation and limits",
        "",
        "Unresolved and removal-only IDs need an explicit eligibility policy. Pre-share events "
        "must not introduce future metadata into training. Multiple article events can multiply "
        "interaction rows in a naive contentId join. Repeat user/article events require an "
        "aggregation rule rather than automatic deduplication.",
        "",
    ]
    lines += [f"- {note}" for note in report["notes"]]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output_dir: Path, raw_dir: Path) -> None:
    """Keep generated reports outside the protected source directory."""
    if output_dir.resolve().is_relative_to(raw_dir.resolve()):
        raise ProfileError("output directory must be outside raw-dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "profile.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (output_dir / "profile.md").write_text(markdown(report))
