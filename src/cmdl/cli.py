"""Command line entry points."""

import argparse
import sys
from pathlib import Path
from uuid import UUID

from cmdl.data.profile import ProfileError, profile, write_report


def _ingest_command(args) -> int:
    import psycopg

    from cmdl.data.ingest import (
        IngestError,
        ingest,
        validate_output,
        write_failure,
        write_import_report,
    )

    def failed(stage, message):
        print(f"Import failed: {message}", file=sys.stderr)
        try:
            write_failure(args.output_dir, args.raw_dir, stage, message)
        except (OSError, IngestError):
            print("Could not write failure diagnostic outside raw-dir.", file=sys.stderr)
        return 1

    try:
        validate_output(args.output_dir, args.raw_dir)
        with psycopg.connect(connect_timeout=5) as connection:
            report, created = ingest(connection, args.raw_dir)
    except (ProfileError, IngestError, OSError) as exc:
        return failed("input_or_provenance", str(exc))
    except psycopg.Error as exc:
        return failed(
            "database",
            f"Database error {exc.sqlstate or 'connection'}; rerun to verify import history.",
        )
    try:
        write_import_report(report, args.output_dir, args.raw_dir)
    except OSError:
        return failed(
            "export", "Database import is committed but report export failed; rerun to export."
        )
    state = "Imported" if created else "Already imported"
    print(f"{state}: {report['import_id']}. Audit: {args.output_dir / 'import.json'}")
    return 0


def _snapshot_command(args) -> int:
    import psycopg

    from cmdl.data.ingest import IngestError, validate_output, write_failure
    from cmdl.data.snapshots import prepare_benchmark, write_benchmark_report
    from cmdl.data.temporal import TemporalError

    try:
        validate_output(args.output_dir, Path("data/raw"))
        with psycopg.connect(connect_timeout=5) as connection:
            report, created = prepare_benchmark(connection, args.import_id, args.variant_id)
    except (TemporalError, IngestError, psycopg.Error) as exc:
        message = (
            f"Database error {exc.sqlstate or 'connection'}; rerun to verify publication."
            if isinstance(exc, psycopg.Error)
            else str(exc)
        )
        print(f"Snapshot preparation failed: {message}", file=sys.stderr)
        try:
            write_failure(args.output_dir, Path("data/raw"), "snapshot", message)
        except (OSError, IngestError):
            pass
        return 1
    try:
        write_benchmark_report(report, args.output_dir)
    except OSError:
        print(
            "Snapshots committed but export failed; rerun to regenerate the report.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{'Published' if created else 'Already published'} benchmark snapshots. "
        f"Report: {args.output_dir / 'benchmark.md'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("profile", help="Verify raw CSVs and write JSON/Markdown profiles")
    check.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    check.add_argument("--output-dir", type=Path, default=Path("artifacts/data-profile"))
    importer = commands.add_parser("ingest", help="Validate and transactionally import both CSVs")
    importer.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    importer.add_argument("--output-dir", type=Path, default=Path("artifacts/source-import"))
    snapshot = commands.add_parser("snapshot", help="Publish validation/test request snapshots")
    snapshot.add_argument("--import-id", type=UUID, required=True)
    snapshot.add_argument("--variant-id", default="original")
    snapshot.add_argument("--output-dir", type=Path, default=Path("artifacts/snapshots"))
    commands.add_parser("migrate", help="Apply pending PostgreSQL migrations using PG* settings")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        return _snapshot_command(args)
    if args.command == "ingest":
        return _ingest_command(args)
    if args.command == "migrate":
        import psycopg

        from cmdl.db.migrate import MigrationError, migrate

        try:
            with psycopg.connect(connect_timeout=5) as connection:
                applied = migrate(connection)
        except MigrationError as exc:
            print(f"Migration failed: {exc}.", file=sys.stderr)
            return 1
        except psycopg.Error as exc:
            # Do not echo connection strings/passwords from libpq errors.
            print(
                f"Migration failed (database error {exc.sqlstate or 'connection'}). "
                "Reconnect and rerun to verify migration history.",
                file=sys.stderr,
            )
            return 1
        print("Applied: " + ", ".join(applied) if applied else "Database is up to date.")
        return 0
    try:
        report = profile(args.raw_dir)
        write_report(report, args.output_dir, args.raw_dir)
    except (ProfileError, OSError) as exc:
        print(f"Profile failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified source files. Report: {args.output_dir / 'profile.md'}")
    return 0
