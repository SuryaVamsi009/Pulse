"""Tests for the Gate 0 runtime route guard configuration boundary."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_SCRIPT = REPO_ROOT / "scripts" / "gate0_guard_check.py"


def load_guard_module():
    spec = importlib.util.spec_from_file_location("gate0_guard_check", GUARD_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_vendor_path(root: Path) -> Path:
    vendor_path = root / "openalgo"
    vendor_path.mkdir(parents=True)
    return vendor_path


def test_env_path_is_used_before_repository_path(tmp_path, monkeypatch):
    guard = load_guard_module()
    env_vendor = fake_vendor_path(tmp_path / "env")
    repo_vendor = tmp_path / "repo" / "vendor" / "openalgo"
    repo_vendor.mkdir(parents=True)
    monkeypatch.setenv("STYX_OPENALGO_PATH", str(env_vendor))

    assert guard.resolve_vendor_path(tmp_path / "repo") == env_vendor.resolve()


def test_repository_relative_vendor_path_is_used(tmp_path, monkeypatch):
    guard = load_guard_module()
    repo_vendor = tmp_path / "repo" / "vendor" / "openalgo"
    repo_vendor.mkdir(parents=True)
    monkeypatch.delenv("STYX_OPENALGO_PATH", raising=False)

    assert guard.resolve_vendor_path(tmp_path / "repo") == repo_vendor.resolve()


def test_running_from_vendor_directory_uses_current_directory(tmp_path, monkeypatch):
    guard = load_guard_module()
    vendor_path = fake_vendor_path(tmp_path)
    monkeypatch.delenv("STYX_OPENALGO_PATH", raising=False)

    assert guard.resolve_vendor_path(vendor_path) == vendor_path.resolve()


def test_missing_vendor_path_fails_clearly(tmp_path, monkeypatch):
    guard = load_guard_module()
    monkeypatch.delenv("STYX_OPENALGO_PATH", raising=False)

    with pytest.raises(guard.GuardConfigurationError, match="OpenAlgo vendor not found"):
        guard.resolve_vendor_path(tmp_path)


def test_invalid_explicit_vendor_path_does_not_fall_back(tmp_path, monkeypatch):
    guard = load_guard_module()
    repo_vendor = tmp_path / "vendor" / "openalgo"
    repo_vendor.mkdir(parents=True)
    monkeypatch.setenv("STYX_OPENALGO_PATH", str(tmp_path / "missing-openalgo"))

    with pytest.raises(guard.GuardConfigurationError, match="STYX_OPENALGO_PATH"):
        guard.resolve_vendor_path(tmp_path)


def test_expected_vendor_revision_is_required(tmp_path):
    guard = load_guard_module()
    vendor_path = fake_vendor_path(tmp_path)
    expected_revision = guard.EXPECTED_VENDOR_REVISION

    def fake_git_runner(command, **kwargs):
        assert command == ["git", "-C", str(vendor_path), "rev-parse", "HEAD"]
        return subprocess.CompletedProcess(command, 0, f"{expected_revision}\n", "")

    assert guard.require_expected_vendor_revision(vendor_path, runner=fake_git_runner) == (
        expected_revision
    )


def test_wrong_vendor_revision_is_rejected(tmp_path):
    guard = load_guard_module()
    vendor_path = fake_vendor_path(tmp_path)

    def fake_git_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "wrong-revision\n", "")

    with pytest.raises(guard.GuardConfigurationError, match="revision mismatch"):
        guard.require_expected_vendor_revision(vendor_path, runner=fake_git_runner)


def test_main_exits_one_for_wrong_vendor_revision(tmp_path, monkeypatch, capsys):
    guard = load_guard_module()
    vendor_path = fake_vendor_path(tmp_path)
    monkeypatch.setenv("STYX_OPENALGO_PATH", str(vendor_path))
    monkeypatch.setattr(guard, "get_vendor_revision", lambda path, runner=None: "wrong-revision")

    assert guard.main() == 1
    assert "revision mismatch" in capsys.readouterr().err


def test_main_exits_one_when_vendor_is_missing(tmp_path, monkeypatch, capsys):
    guard = load_guard_module()
    monkeypatch.setenv("STYX_OPENALGO_PATH", str(tmp_path / "missing-openalgo"))

    assert guard.main() == 1
    assert "OpenAlgo vendor not found" in capsys.readouterr().err


def test_json_output_records_vendor_revision(capsys):
    guard = load_guard_module()

    assert (
        guard.emit_results(
            [{"case": "example", "expected": "x", "observed": "x", "ok": True}],
            guard.EXPECTED_VENDOR_REVISION,
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["vendor_revision"] == guard.EXPECTED_VENDOR_REVISION
