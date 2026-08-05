"""
Implements: Section 3's base_scanner.py CI hook, as an automated test
(not just a manual `make` target) -- "CI pre-commit hook: grep -r
"^import httpx\\|^from httpx\\|^import requests" core/scanners/ -> must
return empty."
Blueprint: bb_agent_v6.6_final_blueprint.md

Runs the IDENTICAL grep pattern the root Makefile's `ci-scanner-http-check`
target uses, via `subprocess`, rather than reimplementing the check in
pure Python -- one pattern, two invocation surfaces (CI/pre-commit via
`make`; this repo's own test suite via `pytest`), not two maintained
copies of the same rule that could drift apart.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_no_direct_httpx_or_requests_imports_in_core_scanners():
    """Section 3's base_scanner.py contract, enforced. If this fails,
    the offending file(s) and line(s) appear in the assertion message
    (grep's own `-n` output), the same information `make
    ci-scanner-http-check` would print."""
    result = subprocess.run(
        ["grep", "-rn", r"^import httpx\|^from httpx\|^import requests", "core/scanners/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # grep exits 0 when it FINDS a match (a violation) and 1 when it
    # finds nothing (the desired, clean state) -- inverted from the
    # usual "0 = success" convention, so the assertion is on stdout
    # being empty, not on the exit code.
    assert result.stdout == "", (
        "Direct httpx/requests import(s) found in core/scanners/ -- all "
        "scanner HTTP calls MUST use self.session (RateLimitedClient), "
        f"per Section 3's base_scanner.py contract:\n{result.stdout}"
    )


def test_core_scanners_directory_exists():
    """Sanity guard: if core/scanners/ is ever renamed/moved, the grep
    above would silently start matching nothing (a false OK) rather than
    failing loudly -- this catches that specific failure mode."""
    assert (REPO_ROOT / "core" / "scanners").is_dir()
