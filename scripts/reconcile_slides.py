"""Reconcile numeric claims in the canonical Part 2 deck with verified source bytes."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from cmdl.data.profile import ProfileError, profile


def reconcile(deck: Path, raw_dir: Path) -> dict:
    report = profile(raw_dir)
    files = report["files"]
    flags = report["interaction_anomalies"]
    claims = [
        (2, f"{report['shared_metadata']['rows']:,} shared articles"),
        (2, f"{files['users_interactions.csv']['rows']:,} interaction events"),
        (2, f"{files['users_interactions.csv']['distinct_ids']['personId']:,} distinct users"),
        (
            15,
            f"{files['users_interactions.csv']['exact_duplicate_excess_rows']} "
            "exact repeated interaction rows",
        ),
        (
            15,
            f"{flags['removal_only_content_id']['rows']} interactions across "
            f"{flags['removal_only_content_id']['content_ids']} removal-only IDs",
        ),
        (15, f"{flags['before_first_share']['rows']} interactions before first share"),
        (15, "All shared rows have title, text, URL, and language"),
    ]
    if any(report["shared_metadata"]["missing_core_fields"].values()):
        raise ValueError("Shared article core fields are incomplete")
    checked = []
    with ZipFile(deck) as archive:
        for slide_number, phrase in claims:
            slide = ET.fromstring(archive.read(f"ppt/slides/slide{slide_number}.xml"))
            texts = [node.text for node in slide.iter() if node.tag.endswith("}t") and node.text]
            if phrase not in " ".join(texts):
                raise ValueError(f"Slide {slide_number}: measured claim not found: {phrase}")
            checked.append({"slide": slide_number, "claim": phrase, "matches_source": True})
    return {
        "deck": deck.name,
        "sha256": hashlib.sha256(deck.read_bytes()).hexdigest(),
        "checked_claims": checked,
        "source_checksums": {name: value["sha256"] for name, value in files.items()},
        "note": "Checks the current final Part 2 deck, not archived proposals or future decks.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deck", type=Path)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    try:
        print(json.dumps(reconcile(args.deck, args.raw_dir), indent=2))
    except (OSError, ValueError, KeyError, BadZipFile, ET.ParseError, ProfileError) as exc:
        parser.exit(1, f"Slide reconciliation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
