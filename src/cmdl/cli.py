"""Command line entry points."""

import argparse
import sys
from pathlib import Path

from cmdl.data.profile import ProfileError, profile, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("profile", help="Verify raw CSVs and write JSON/Markdown profiles")
    check.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    check.add_argument("--output-dir", type=Path, default=Path("artifacts/data-profile"))
    commands.add_parser("migrate", help="Apply pending PostgreSQL migrations using PG* settings")
    args = parser.parse_args(argv)
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
