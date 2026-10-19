"""Compare a downloaded publisher version-5 archive against the local raw manifest."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def verify(metadata_path: Path, archive_path: Path, raw_dir: Path) -> list[dict]:
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("ref") != "gspmoreira/articles-sharing-reading-from-cit-deskdrop":
        raise ValueError("Unexpected publisher dataset identity")
    if metadata.get("licenseName") != "Database: Open Database, Contents: Database Contents":
        raise ValueError(
            "Publisher license declaration differs; review terms before updating evidence"
        )
    if not any(v.get("versionNumber") == 5 for v in metadata.get("versions", [])):
        raise ValueError("Publisher metadata does not identify version 5")
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    results = []
    with ZipFile(archive_path) as archive:
        for entry in manifest["files"]:
            name = entry["file"]
            upstream = archive.read(name)
            local = (raw_dir / name).read_bytes()
            expected = (entry["sha256"], entry["bytes"])
            for source, payload in [("upstream", upstream), ("local", local)]:
                if (hashlib.sha256(payload).hexdigest(), len(payload)) != expected:
                    raise ValueError(f"{name}: {source} checksum/size mismatch")
            results.append({"file": name, "sha256": expected[0], "bytes": expected[1]})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.metadata, args.archive, args.raw_dir), indent=2))
    except (OSError, ValueError, KeyError, BadZipFile) as exc:
        parser.exit(1, f"Upstream verification failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
