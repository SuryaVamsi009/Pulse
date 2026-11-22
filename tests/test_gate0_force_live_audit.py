import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "gate0_force_live_audit.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("gate0_force_live_audit", AUDIT_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_require_expected_vendor_revision_matches():
    audit = load_audit_module()
    mock_runner = MagicMock()
    mock_runner.return_value.returncode = 0
    mock_runner.return_value.stdout = audit.EXPECTED_VENDOR_REVISION + "\n"

    revision = audit.require_expected_vendor_revision(Path("/fake/path"), runner=mock_runner)
    assert revision == audit.EXPECTED_VENDOR_REVISION


def test_require_expected_vendor_revision_mismatch():
    audit = load_audit_module()
    mock_runner = MagicMock()
    mock_runner.return_value.returncode = 0
    mock_runner.return_value.stdout = "wrong_revision\n"

    with pytest.raises(audit.GuardConfigurationError, match="OpenAlgo vendor revision mismatch"):
        audit.require_expected_vendor_revision(Path("/fake/path"), runner=mock_runner)


def test_audit_vendor_pass(tmp_path):
    audit = load_audit_module()
    # Setup allowed file with the call site
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    allowed_file = tmp_path / "services" / "strategy_module" / "order_dispatch.py"
    allowed_file.parent.mkdir(parents=True)
    allowed_file.write_text("place_order_with_auth(..., force_live=True)")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)

    # Run audit
    results = audit.audit_vendor(tmp_path)

    assert len(results) == 1
    assert results[0]["file"].endswith("services/strategy_module/order_dispatch.py")
    assert results[0]["allowed"] is True


def test_audit_vendor_fail_new_site(tmp_path):
    audit = load_audit_module()
    # Setup allowed file
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    allowed_file = tmp_path / "services" / "strategy_module" / "order_dispatch.py"
    allowed_file.parent.mkdir(parents=True)
    allowed_file.write_text("place_order_with_auth(..., force_live=True)")

    # Setup disallowed file
    disallowed_file = tmp_path / "services" / "other_service.py"
    disallowed_file.parent.mkdir(parents=True, exist_ok=True)
    disallowed_file.write_text("some_call(force_live=True)")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)

    # Run audit
    results = audit.audit_vendor(tmp_path)

    assert len(results) == 2
    # Find the disallowed result
    disallowed = next(r for r in results if "other_service.py" in r["file"])
    assert disallowed["allowed"] is False


def test_audit_vendor_fail_missing_allowed_site(tmp_path):
    audit = load_audit_module()
    # Allowed file exists but doesn't have the call site
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    allowed_file = tmp_path / "services" / "strategy_module" / "order_dispatch.py"
    allowed_file.parent.mkdir(parents=True)
    allowed_file.write_text("no force live here")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)

    # Run audit
    msg = "Required allowlisted site disappeared"
    with pytest.raises(audit.GuardConfigurationError, match=msg):
        audit.audit_vendor(tmp_path)
