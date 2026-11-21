#!/usr/bin/env python3
"""
Gate 0 Runtime Route-Guard Test

Proves at runtime (no network) that with Analyzer Mode on:
- The Fyers order function is never called for placeorder, placesmartorder,
  basketorder, splitorder
- The sandbox function IS called instead

And with Analyzer Mode off:
- The Fyers order function IS called (negative control proving the test can
  detect a live call)

OpenAlgo is selected in this order:
1. STYX_OPENALGO_PATH
2. ./vendor/openalgo relative to the current directory
3. the current directory when run from vendor/openalgo

These checks stay in this script because the selected checkout and its git
revision are runtime facts that configuration alone cannot verify.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

EXPECTED_VENDOR_REVISION = "a1b9e32506bce21403a8614dddeed0ab61fd519a"


class GuardConfigurationError(RuntimeError):
    """Raised when the guard cannot establish its required configuration."""


def resolve_vendor_path(cwd: Path | None = None) -> Path:
    """Find the OpenAlgo checkout without using a machine-specific fallback."""
    current_directory = (cwd or Path.cwd()).resolve()
    configured_path = os.environ.get("STYX_OPENALGO_PATH")

    if configured_path:
        explicit_path = Path(configured_path).expanduser()
        if not explicit_path.is_absolute():
            explicit_path = current_directory / explicit_path
        if not explicit_path.is_dir():
            raise GuardConfigurationError(
                f"OpenAlgo vendor not found at STYX_OPENALGO_PATH={explicit_path}"
            )
        return explicit_path.resolve()

    repository_path = current_directory / "vendor" / "openalgo"
    if repository_path.is_dir():
        return repository_path.resolve()

    if current_directory.name == "openalgo" and current_directory.is_dir():
        return current_directory

    raise GuardConfigurationError(
        "OpenAlgo vendor not found. Set STYX_OPENALGO_PATH, run from a repository "
        "with ./vendor/openalgo, or run this command from vendor/openalgo."
    )


def get_vendor_revision(
    vendor_path: Path, runner=subprocess.run
) -> str:
    """Read the exact git revision of the selected OpenAlgo checkout."""
    command = ["git", "-C", str(vendor_path), "rev-parse", "HEAD"]
    try:
        result = runner(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise GuardConfigurationError(
            f"Unable to read OpenAlgo vendor revision with git: {error}"
        ) from error

    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        raise GuardConfigurationError(
            f"Unable to read OpenAlgo vendor revision from {vendor_path}{suffix}"
        )

    revision = result.stdout.strip()
    if not revision:
        raise GuardConfigurationError(f"OpenAlgo vendor revision is empty: {vendor_path}")
    return revision


def require_expected_vendor_revision(vendor_path: Path, runner=subprocess.run) -> str:
    """Fail closed unless the selected checkout is the Gate 0 audited revision."""
    revision = get_vendor_revision(vendor_path, runner=runner)
    if revision != EXPECTED_VENDOR_REVISION:
        raise GuardConfigurationError(
            "OpenAlgo vendor revision mismatch: "
            f"expected {EXPECTED_VENDOR_REVISION}, got {revision}"
        )
    return revision


def prepare_isolated_environment() -> None:
    """Set temporary databases and non-secret test values before vendor imports."""
    temp_dir = tempfile.mkdtemp(prefix="gate0_guard_")
    os.environ["DATABASE_URL"] = f"sqlite:///{temp_dir}/openalgo.db"
    os.environ["SANDBOX_DATABASE_URL"] = f"sqlite:///{temp_dir}/sandbox.db"
    os.environ["LATENCY_DATABASE_URL"] = f"sqlite:///{temp_dir}/latency.db"
    os.environ["LOGS_DATABASE_URL"] = f"sqlite:///{temp_dir}/logs.db"
    os.environ["HEALTH_DATABASE_URL"] = f"sqlite:///{temp_dir}/health.db"
    os.environ["HISTORIFY_DATABASE_URL"] = f"{temp_dir}/historify.duckdb"

    # Required by auth_db; these are test-only values, not broker credentials.
    os.environ["API_KEY_PEPPER"] = "a" * 64
    os.environ["FERNET_SALT"] = "b" * 64
    os.environ["SMTP_KEY_SALT"] = "smtp-test-salt"

    os.environ["BROKER_API_KEY"] = "test_broker_key"
    os.environ["BROKER_API_SECRET"] = "test_broker_secret"
    os.environ["APP_KEY"] = "c" * 64
    os.environ["VALID_BROKERS"] = "fyers"
    os.environ["FLASK_HOST_IP"] = "127.0.0.1"
    os.environ["FLASK_PORT"] = "5000"
    os.environ["FLASK_DEBUG"] = "False"
    os.environ["FLASK_ENV"] = "development"
    os.environ["HOST_SERVER"] = "http://127.0.0.1:5000"
    os.environ["NGROK_ALLOW"] = "FALSE"


# =============================================================================
# TEST HARNESS
# =============================================================================

call_log = {
    "fyers_place_order_api": 0,
    "fyers_place_smartorder_api": 0,
    "sandbox_place_order": 0,
    "sandbox_place_smart_order": 0,
}


def reset_call_log():
    for key in call_log:
        call_log[key] = 0


def mock_fyers_place_order_api(*args, **kwargs):
    call_log["fyers_place_order_api"] += 1
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.status_code = 200
    return mock_resp, {"s": "ok", "id": "test_order_id"}, "test_order_id"


def mock_fyers_place_smartorder_api(*args, **kwargs):
    call_log["fyers_place_smartorder_api"] += 1
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.status_code = 200
    return mock_resp, {"s": "ok", "id": "test_smart_order_id"}, "test_smart_order_id"


def mock_sandbox_place_order(*args, **kwargs):
    call_log["sandbox_place_order"] += 1
    return True, {"status": "success", "orderid": "sandbox_order_id", "mode": "analyze"}, 200


def mock_sandbox_place_smart_order(*args, **kwargs):
    call_log["sandbox_place_smart_order"] += 1
    return (
        True,
        {"status": "success", "orderid": "sandbox_smart_order_id", "mode": "analyze"},
        200,
    )


def mock_get_auth_token_broker(api_key):
    return "test_auth_token", "fyers"


def mock_verify_api_key(api_key):
    return "test_user_id"


def mock_get_analyze_mode():
    return getattr(mock_get_analyze_mode, "_value", True)


def initialize_vendor(vendor_path: Path) -> None:
    """Import and initialize OpenAlgo only after isolation and path selection."""
    sys.path.insert(0, str(vendor_path))

    from database.auth_db import init_db as init_auth_db  # noqa: E402
    from database.sandbox_db import init_db as init_sandbox_db  # noqa: E402
    from database.settings_db import init_db as init_settings_db  # noqa: E402

    init_auth_db()
    init_settings_db()
    init_sandbox_db()

    mock_order_schema = MagicMock()
    mock_order_schema.load = lambda data: data
    sys.modules["restx_api.schemas"] = MagicMock()
    sys.modules["restx_api.schemas"].OrderSchema = lambda: mock_order_schema
    sys.modules["restx_api"] = MagicMock()
    sys.modules["restx_api.options_multiorder"] = MagicMock()
    sys.modules["restx_api.options_multiorder.api"] = MagicMock()
    sys.modules["services.options_multiorder_service"] = MagicMock()

    import database.auth_db  # noqa: E402
    import database.settings_db  # noqa: E402

    database.settings_db.get_analyze_mode = mock_get_analyze_mode
    database.auth_db.get_auth_token_broker = mock_get_auth_token_broker
    database.auth_db.verify_api_key = mock_verify_api_key


def run_case(
    case_name,
    analyzer_on,
    order_func_name,
    order_data,
    expected_sandbox,
    expected_fyers,
    api_key,
):
    """Run a single test case and return result dict."""
    reset_call_log()
    mock_get_analyze_mode._value = analyzer_on

    if order_func_name == "place_order":
        from services.place_order_service import place_order as order_func
    elif order_func_name == "place_smart_order":
        from services.place_smart_order_service import place_smart_order as order_func
    elif order_func_name == "place_basket_order":
        from services.basket_order_service import place_basket_order as order_func
    elif order_func_name == "split_order":
        from services.split_order_service import split_order as order_func
    else:
        raise ValueError(f"Unknown order function: {order_func_name}")

    with (
        patch("services.sandbox_service.sandbox_place_order", mock_sandbox_place_order),
        patch(
            "services.sandbox_service.sandbox_place_smart_order",
            mock_sandbox_place_smart_order,
        ),
        patch("broker.fyers.api.order_api.place_order_api", mock_fyers_place_order_api),
        patch(
            "broker.fyers.api.order_api.place_smartorder_api",
            mock_fyers_place_smartorder_api,
        ),
    ):
        try:
            success, response, status_code = order_func(order_data, api_key=api_key)
            observed_sandbox = (
                call_log["sandbox_place_order"] + call_log["sandbox_place_smart_order"]
            )
            observed_fyers = (
                call_log["fyers_place_order_api"] + call_log["fyers_place_smartorder_api"]
            )
        except Exception as error:
            return {
                "case": case_name,
                "expected": f"sandbox={expected_sandbox}, fyers={expected_fyers}",
                "observed": f"ERROR: {error}",
                "ok": False,
            }

    ok = (observed_sandbox == expected_sandbox) and (observed_fyers == expected_fyers)
    return {
        "case": case_name,
        "expected": f"sandbox={expected_sandbox}, fyers={expected_fyers}",
        "observed": f"sandbox={observed_sandbox}, fyers={observed_fyers}",
        "ok": ok,
    }


def run_guard_cases():
    """Run the four Analyzer Mode routes and the live-mode negative control."""
    test_api_key = "test_api_key_123"
    base_order = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "quantity": 1,
        "pricetype": "MARKET",
        "product": "MIS",
        "strategy": "test_strategy",
    }
    smart_order = {**base_order, "position_size": 0}
    basket_order = {"apikey": test_api_key, "strategy": "test_strategy", "orders": [base_order]}
    split_order = {**base_order, "total_quantity": 1, "splitsize": 1}

    return [
        run_case(
            "analyzer_on_placeorder",
            analyzer_on=True,
            order_func_name="place_order",
            order_data=base_order,
            expected_sandbox=1,
            expected_fyers=0,
            api_key=test_api_key,
        ),
        run_case(
            "analyzer_on_placesmartorder",
            analyzer_on=True,
            order_func_name="place_smart_order",
            order_data=smart_order,
            expected_sandbox=1,
            expected_fyers=0,
            api_key=test_api_key,
        ),
        run_case(
            "analyzer_on_basketorder",
            analyzer_on=True,
            order_func_name="place_basket_order",
            order_data=basket_order,
            expected_sandbox=1,
            expected_fyers=0,
            api_key=test_api_key,
        ),
        run_case(
            "analyzer_on_splitorder",
            analyzer_on=True,
            order_func_name="split_order",
            order_data=split_order,
            expected_sandbox=1,
            expected_fyers=0,
            api_key=test_api_key,
        ),
        run_case(
            "analyzer_off_placeorder_negative_control",
            analyzer_on=False,
            order_func_name="place_order",
            order_data=base_order,
            expected_sandbox=0,
            expected_fyers=1,
            api_key=test_api_key,
        ),
    ]


def emit_results(results, vendor_revision: str) -> int:
    """Print JSON results, including the audited vendor revision."""
    all_ok = True
    for result in results:
        output = {**result, "vendor_revision": vendor_revision}
        print(json.dumps(output))
        all_ok = all_ok and result["ok"]
    return 0 if all_ok else 1


def main() -> int:
    try:
        vendor_path = resolve_vendor_path()
        vendor_revision = require_expected_vendor_revision(vendor_path)
        prepare_isolated_environment()
        initialize_vendor(vendor_path)
    except GuardConfigurationError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1

    return emit_results(run_guard_cases(), vendor_revision)


if __name__ == "__main__":
    sys.exit(main())
