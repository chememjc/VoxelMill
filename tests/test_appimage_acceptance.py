"""Focused contract tests for the external AppImage acceptance runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "appimage_acceptance.py"


@pytest.fixture(scope="module")
def acceptance():
    spec = importlib.util.spec_from_file_location("appimage_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checksum_streams_the_artifact(tmp_path, acceptance):
    artifact = tmp_path / "VoxelMill.AppImage"
    artifact.write_bytes(b"published artifact bytes")
    assert acceptance._sha256(artifact) == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_release_defaults_and_removed_setting_are_required(acceptance):
    report = {"settings": {"support": {
        "brace_spacing_mm": 5.0,
        "brace_max_length_mm": 30.0,
        "allow_part_to_part": True,
        "brace_model_pillars": False,
    }}}
    evidence = acceptance._assert_release_settings(report, "0.6.0")
    assert evidence == {
        "version": "0.6.0", "brace_spacing_mm": 5.0,
        "brace_max_length_mm": 30.0, "allow_part_to_part": True,
    }
    report["settings"]["support"]["brace_start_height_mm"] = 0.0
    with pytest.raises(AssertionError):
        acceptance._assert_release_settings(report, "0.6.0")


def test_runtime_environment_is_isolated_from_the_development_venv(tmp_path, acceptance):
    appdir = tmp_path / "squashfs-root"
    env = acceptance._runtime_environment(appdir, tmp_path / "out")
    assert env["PYTHONHOME"] == str(appdir / "usr")
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONPATH"].startswith(str(appdir))
    assert "VIRTUAL_ENV" not in env


def test_rerun_removes_only_owned_stale_evidence(tmp_path, acceptance):
    for name in ("prepared.stl", "verify.json", "gui.json"):
        (tmp_path / name).write_text("stale")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "stale").write_text("old preference")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("user evidence")

    acceptance._reset_output(tmp_path)

    assert not (tmp_path / "prepared.stl").exists()
    assert not (tmp_path / "verify.json").exists()
    assert not (tmp_path / "gui.json").exists()
    assert not (tmp_path / "config").exists()
    assert unrelated.read_text() == "user evidence"


def test_runner_records_failures_without_losing_process_output(tmp_path, acceptance):
    with pytest.raises(RuntimeError) as error:
        acceptance._run(
            ["/bin/sh", "-c", "echo packaged-out; echo packaged-err >&2; exit 7"],
            cwd=tmp_path,
        )
    evidence = json.loads(str(error.value))
    assert evidence["returncode"] == 7
    assert "packaged-out" in evidence["stdout_tail"]
    assert "packaged-err" in evidence["stderr_tail"]
