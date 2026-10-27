"""Content-addressed local run tracking; no model or holdout selection is implicit."""

import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


class RunError(ValueError):
    """A run cannot be reproduced or its artifacts failed verification."""


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def environment(repository: Path) -> dict:
    """Require a clean tracked checkout and retain actual installed dependencies."""

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repository), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        revision = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=all")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunError("Run capture requires a Git checkout") from exc
    if dirty:
        raise RunError("Commit code and configuration before capturing a reproducible run")
    lock = repository / "uv.lock"
    return {
        "code_revision": revision,
        "lock_sha256": checksum(lock.read_bytes()),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": sorted(
            {f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()}
        ),
    }


def create_run(root: Path, *, provenance: dict, snapshot: dict, config: dict) -> Path:
    """Publish an immutable run directory atomically, or verify an identical replay.

    config requires explicit model/transform versions, parameters, seeds and tolerance.
    Artifact source paths are excluded from identity; their bytes and names are pinned.
    """
    if not isinstance(config, dict):
        raise RunError("Configuration must be an object")
    required = {"model", "transformations", "parameters", "seeds", "numeric_tolerance"}
    if not required <= config.keys() or not config["model"] or not config["transformations"]:
        raise RunError(
            "Configuration requires model, transformations, parameters, seeds and tolerance"
        )
    if not isinstance(config["parameters"], dict) or not isinstance(config["seeds"], dict):
        raise RunError("Parameters and seeds must be explicit objects")
    if not config["seeds"] or any(type(v) is not int for v in config["seeds"].values()):
        raise RunError("Record at least one integer seed")
    tolerance = config["numeric_tolerance"]
    if type(tolerance) not in (int, float) or tolerance < 0:
        raise RunError("Numeric tolerance must be a finite nonnegative number")
    if not snapshot.get("descriptor") or not snapshot.get("snapshot_id"):
        raise RunError("A published snapshot descriptor and ID are required")
    sources = config.get("artifacts", {})
    if not isinstance(sources, dict):
        raise RunError("Artifacts must map simple filenames to local paths")
    payloads = {}
    for name, source in sources.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise RunError("Artifact names must be simple filenames")
        if name == "manifest.json":
            raise RunError("manifest.json is reserved")
        payloads[name] = Path(source).read_bytes()
    manifest = {
        "tracking_version": "cmdl-run-v1",
        "environment": provenance,
        "snapshot": snapshot,
        "configuration": {k: v for k, v in config.items() if k != "artifacts"},
        "artifacts": {
            name: {"sha256": checksum(data), "size_bytes": len(data)}
            for name, data in payloads.items()
        },
    }
    encoded = canonical(manifest)
    run_id = checksum(encoded)
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / run_id
    staging = Path(tempfile.mkdtemp(prefix=".run-", dir=root))
    try:
        for name, data in payloads.items():
            (staging / name).write_bytes(data)
        (staging / "manifest.json").write_bytes(encoded)
        try:
            staging.rename(target)
        except OSError:
            if not target.is_dir():
                raise
            verify_run(target)
            if (target / "manifest.json").read_bytes() != encoded:
                raise RunError("Existing run manifest differs") from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def verify_run(path: Path) -> dict:
    """Detect changed manifests, missing artifacts and byte drift without loading models."""
    encoded = (path / "manifest.json").read_bytes()
    if checksum(encoded) != path.name:
        raise RunError("Manifest checksum does not match run identity")
    manifest = json.loads(encoded)
    for name, expected in manifest["artifacts"].items():
        if Path(name).name != name or name in (".", ".."):
            raise RunError("Invalid artifact path")
        data = (path / name).read_bytes()
        if checksum(data) != expected["sha256"] or len(data) != expected["size_bytes"]:
            raise RunError(f"Artifact checksum differs: {name}")
    return manifest
