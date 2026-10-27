"""Run identity must distinguish inputs and detect corrupt/replaced outputs."""

import copy

import pytest

from cmdl.runs import RunError, create_run, verify_run


@pytest.fixture
def inputs(tmp_path):
    ranking = tmp_path / "ranking.json"
    ranking.write_text('["1","2"]')
    return dict(
        provenance={"code_revision": "a" * 40, "dependencies": ["cmdl==0.1.0"]},
        snapshot={"snapshot_id": "explicit-snapshot", "descriptor": {"files": ["source-hash"]}},
        config={
            "model": "fixture-v1",
            "transformations": ["fixture-input-v1"],
            "parameters": {"k": 2},
            "seeds": {"model": 42},
            "numeric_tolerance": 0,
            "artifacts": {"ranking.json": str(ranking)},
        },
    )


def test_replay_and_artifact_integrity(tmp_path, inputs):
    root = tmp_path / "runs"
    first = create_run(root, **inputs)
    assert create_run(root, **inputs) == first
    assert verify_run(first)["configuration"]["numeric_tolerance"] == 0
    (first / "ranking.json").write_text('["2","1"]')
    with pytest.raises(RunError, match="checksum"):
        verify_run(first)
    with pytest.raises(RunError, match="checksum"):
        create_run(root, **inputs)


@pytest.mark.parametrize("field", ["provenance", "snapshot", "parameters", "seeds", "artifacts"])
def test_changed_inputs_have_distinct_identity(tmp_path, inputs, field):
    first = create_run(tmp_path / "runs", **inputs)
    changed = copy.deepcopy(inputs)
    if field in ("provenance", "snapshot"):
        changed[field]["version"] = "changed"
    elif field == "artifacts":
        from pathlib import Path

        Path(inputs["config"]["artifacts"]["ranking.json"]).write_text('["2","1"]')
    else:
        changed["config"][field]["model"] = 7
    assert create_run(tmp_path / "runs", **changed) != first


def test_invalid_config_and_path_escape(tmp_path, inputs):
    inputs["config"]["numeric_tolerance"] = float("nan")
    with pytest.raises(ValueError):
        create_run(tmp_path / "runs", **inputs)
    inputs["config"]["numeric_tolerance"] = 0
    inputs["config"]["artifacts"] = {"../escape": "unused"}
    with pytest.raises(RunError, match="filenames"):
        create_run(tmp_path / "runs", **inputs)


def test_manifest_tampering(tmp_path, inputs):
    path = create_run(tmp_path / "runs", **inputs)
    (path / "manifest.json").write_text("{}")
    with pytest.raises(RunError, match="identity"):
        verify_run(path)


def test_environment_requires_clean_checkout(tmp_path):
    import subprocess

    from cmdl.runs import environment

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    (tmp_path / "uv.lock").write_text("locked")
    git("add", "uv.lock")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture")
    captured = environment(tmp_path)
    assert len(captured["code_revision"]) == 40
    assert captured["dependencies"]
    (tmp_path / "new-code.py").write_text("changed")
    with pytest.raises(RunError, match="Commit"):
        environment(tmp_path)
