#!/usr/bin/env python3
"""
Gate 0 Static Force-Live Audit

Scans the OpenAlgo vendor checkout for `force_live=True` and ensures only
allowlisted call sites exist. Exits 1 if any disallowed site is found or
if a required allowlisted site disappears.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED_VENDOR_REVISION = "a1b9e32506bce21403a8614dddeed0ab61fd519a"

# List of files and their expected counts of force_live=True
# key: relative path from vendor root
# value: expected number of occurrences
ALLOWLIST = {
    "services/strategy_module/order_dispatch.py": 1,
}


class GuardConfigurationError(RuntimeError):
    """Raised when the audit cannot establish its required configuration."""


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


def get_vendor_revision(vendor_path: Path, runner=subprocess.run) -> str:
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


def audit_vendor(vendor_path: Path) -> list[dict]:
    """Scan vendor for force_live=True and validate against allowlist."""
    # We use git grep if available, or fallback to manual scan if not in a git repo
    # But since require_expected_vendor_revision uses git, we assume git is available.

    results = []

    # Use a set to track which allowlisted sites we found
    found_allowlist = {path: 0 for path in ALLOWLIST}

    # Use git grep to find all occurrences of force_live=True
    # We skip vendor/ as it's the root we are searching from
    try:
        command = ["git", "-C", str(vendor_path), "grep", "-n", "force_live=True"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise GuardConfigurationError(f"Failed to run git grep: {error}") from error

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if not line:
                continue
            # format: path:line_number:content
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue

            rel_path = parts[0]
            line_num = parts[1]
            content = parts[2]

            is_allowed = False
            if rel_path in ALLOWLIST:
                found_allowlist[rel_path] += 1
                is_allowed = True

            results.append(
                {
                    "file": f"vendor/openalgo/{rel_path}",
                    "line": int(line_num),
                    "content": content.strip(),
                    "allowed": is_allowed,
                }
            )
    elif result.returncode > 1:
        # 0 = found, 1 = not found, >1 = error
        raise GuardConfigurationError(f"git grep error: {result.stderr}")

    # Verify that all allowlisted sites were found
    for rel_path, expected_count in ALLOWLIST.items():
        if found_allowlist[rel_path] != expected_count:
            raise GuardConfigurationError(
                f"Required allowlisted site disappeared or count mismatch: "
                f"{rel_path} (expected {expected_count}, found {found_allowlist[rel_path]})"
            )

    return results


def main() -> int:
    try:
        vendor_path = resolve_vendor_path()
        require_expected_vendor_revision(vendor_path)
        results = audit_vendor(vendor_path)
    except GuardConfigurationError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1

    all_allowed = True
    for res in results:
        print(json.dumps(res))
        if not res["allowed"]:
            all_allowed = False

    return 0 if all_allowed else 1


if __name__ == "__main__":
    sys.exit(main())
