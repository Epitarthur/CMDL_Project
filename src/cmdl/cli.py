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
    args = parser.parse_args(argv)
    try:
        report = profile(args.raw_dir)
        write_report(report, args.output_dir, args.raw_dir)
    except (ProfileError, OSError) as exc:
        print(f"Profile failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified source files. Report: {args.output_dir / 'profile.md'}")
    return 0
