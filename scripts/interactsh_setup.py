#!/usr/bin/env python3
"""
Implements: Section 3 -- scripts/interactsh_setup.py ("Tests public
callback; falls back to self-hosted")
Blueprint: bb_agent_v6.6_final_blueprint.md

Implements the deployment-mode check from Section 4.1 (Hybrid: public
primary interactsh.com, self-hosted Go client fallback) and the failure
cascade from Section 4.3 (public 429 x3 -> self-hosted; self-hosted
failure -> degrade to in-band SSRF only).

WEEK 0 SCOPE NOTE: This is the "interactsh_conn" preflight check's
dependency (Section 3: "public interactsh responds OR self-hosted
starts"). It is runnable standalone now (no dependency on later-week
BB-Agent code), but requires outbound network access to interactsh.com,
which this project's build sandbox does not have (network allowlist is
scoped to package registries and GitHub). Unit tests mock the HTTP layer
end to end; live verification against the real interactsh.com should be
re-run in an environment with that egress before Week 0 sign-off.

WEEK 7 UPDATE (docs/DECISIONS.md item 81): `InteractshMode` moved to
`core/ontology/enums.py` -- `core/http/interactsh_client.py` (the
runtime client this file's own comments already anticipated, e.g.
line ~84's "InteractshClient's job at runtime") needed the identical
enum, so it is now defined once and imported here, not duplicated.
Behavior of every function below is unchanged; only the enum's location
moved.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import requests

from core.ontology.enums import InteractshMode

PUBLIC_INTERACTSH_HEALTH_URL = "https://interactsh.com/"
PUBLIC_RATE_LIMIT_CONSECUTIVE_429 = 3  # Section 4.3: "Public 429 x 3 -> Switch to self-hosted"
REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_RETRY_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class InteractshSetupResult:
    """Outcome of the interactsh_conn preflight dependency.

    Attributes:
        mode: Which deployment mode is active.
        detail: Human-readable explanation.
    """

    mode: InteractshMode
    detail: str

    @property
    def preflight_ok(self) -> bool:
        # Section 3 preflight check: "public interactsh responds OR
        # self-hosted starts" -- UNAVAILABLE is the only failing state.
        return self.mode in (InteractshMode.PUBLIC, InteractshMode.SELF_HOSTED)


def check_public_interactsh(session: requests.Session | None = None) -> tuple[bool, int | None, str]:
    """Probes the public interactsh.com endpoint once.

    Args:
        session: Injectable HTTP session (used by tests to mock transport).

    Returns:
        (responded_ok, status_code, detail)
    """
    http = session or requests
    try:
        resp = http.get(PUBLIC_INTERACTSH_HEALTH_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return False, None, f"network error: {exc}"

    if resp.status_code == 429:
        return False, 429, "rate limited"
    if 200 <= resp.status_code < 500:
        # Section 4.1: any non-429, non-5xx response counts as "responds"
        # for setup purposes -- full correlation-ID handshake testing is
        # InteractshClient's job at runtime (Section 4.2), not this
        # one-shot preflight probe.
        return True, resp.status_code, "public interactsh reachable"
    return False, resp.status_code, f"unexpected status {resp.status_code}"


def try_self_hosted_fallback(start_fn) -> tuple[bool, str]:
    """Attempts to start the self-hosted Go interactsh client subprocess.

    Args:
        start_fn: Callable that starts the self-hosted client and returns
            True/False for success. Injected so this function -- and its
            tests -- do not hardcode a specific process-launch mechanism
            that belongs to control/process_supervisor.py (not yet built;
            Week 6+).

    Returns:
        (started_ok, detail)
    """
    try:
        started = start_fn()
    except Exception as exc:  # noqa: BLE001 -- boundary to an injected,
        # caller-supplied callable; classified into a clean result rather
        # than propagating an arbitrary exception type up to the preflight
        # runner.
        return False, f"self-hosted start raised: {exc}"
    if started:
        return True, "self-hosted interactsh client started"
    return False, "self-hosted interactsh client failed to start"


def run_setup(
    consecutive_429_limit: int = PUBLIC_RATE_LIMIT_CONSECUTIVE_429,
    session: requests.Session | None = None,
    self_hosted_start_fn=None,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> InteractshSetupResult:
    """Runs the full Section 4.1/4.3 setup sequence.

    Args:
        consecutive_429_limit: Number of consecutive 429s before falling
            back (Section 4.3 default: 3).
        session: Injectable HTTP session for testing.
        self_hosted_start_fn: Injectable self-hosted starter for testing.
            If None and fallback is needed, self-hosted is reported as
            unavailable rather than attempted -- the real Go self-hosted
            client binary does not exist yet (later-week deliverable);
            this script's Week 0 job is the public-path probe and the
            fallback *decision logic*, wired to a real launcher once one
            exists.
        retry_delay_seconds: Delay between consecutive-429 retries. Tests
            pass 0 to avoid real wall-clock waits.

    Returns:
        An InteractshSetupResult.
    """
    consecutive_429s = 0
    last_detail = ""
    public_ok = False

    for attempt in range(consecutive_429_limit):
        ok, status, detail = check_public_interactsh(session)
        last_detail = detail
        if ok:
            public_ok = True
            break
        if status == 429:
            consecutive_429s += 1
            if attempt < consecutive_429_limit - 1:
                time.sleep(retry_delay_seconds)
            continue
        break  # non-429 failure: retrying the identical request won't help

    if public_ok:
        return InteractshSetupResult(mode=InteractshMode.PUBLIC, detail=last_detail)

    # Section 4.3's literal trigger is "Public 429 x3"; this preflight
    # script also falls back on any other public failure (e.g. network
    # error, 5xx) after a single attempt, since the goal here is a binary
    # "is SOME interactsh path usable" answer, not a full replay of the
    # runtime InteractshClient failure cascade (that is InteractshClient's
    # job, Section 4.2/4.3, not this one-shot setup check's).
    if self_hosted_start_fn is not None:
        started, sh_detail = try_self_hosted_fallback(self_hosted_start_fn)
        if started:
            return InteractshSetupResult(mode=InteractshMode.SELF_HOSTED, detail=sh_detail)
        return InteractshSetupResult(
            mode=InteractshMode.UNAVAILABLE,
            detail=f"public failed ({last_detail}); self-hosted failed ({sh_detail})",
        )

    return InteractshSetupResult(
        mode=InteractshMode.UNAVAILABLE,
        detail=f"public failed ({last_detail}); no self-hosted launcher available yet (Week 0)",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    result = run_setup()
    print(f"[INTERACTSH_SETUP] mode={result.mode.value} detail={result.detail!r}")
    return 0 if result.preflight_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
