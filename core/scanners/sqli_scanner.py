"""
Implements: Section 7.2 -- sqli_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE -- CHEAP FAST LANE SIGNALS, NOT THE FULL VERIFICATION ALGORITHM:
Section 7.2's boolean-blind detail ("Repeat both boolean probes 5 times
each (10 requests total)... boolean_differential_confirmed fires per the
precise stability + non-overlap rule in Section 5.2") and its time-based
detail ("SLEEP(5), 2.5σ baseline") are both `sqli_verifier.py`'s job
(Section 3: "2.5σ statistical baseline") -- a file that does not exist
yet (grep-confirmed before this scanner was written), consistent with it
being a separate, not-yet-built pipeline stage, the same scope boundary
`xss_scanner.py` already documented for `xss_verifier.py`. This scanner
does ONE request per error/union probe, ONE baseline + ONE probe per
time-based check, and ONE true/false pair (not five) per boolean check
-- cheap, broad, single-pass signals meant to produce a CANDIDATE for
later, more expensive confirmation, not a statistically rigorous proof
by itself. `EvidenceChain`/evidence-type computation (Section 5,
`core/verifier/evidence_chain.py`, already built Week 1) is not touched
by this scanner either -- out of scope, same reasoning.

FOUR TECHNIQUES, FOUR PAYLOAD SHAPES (`sqli_payloads.json`'s own
`_status` note points here for the authoritative contract):
  - `error`: `payload_template` only, no `{marker}`. Inject; check
    response text (case-insensitive substring) against
    `DB_ERROR_SIGNATURES` below.
  - `union`: `payload_template` with a `{marker}` placeholder, resolved
    the same random-per-probe way `xss_scanner.py` resolves its own
    (module docstring there explains why a literal marker is wrong).
    Section 7.2's own example: `UNION SELECT NULL,'XBOW_PROBE_12345',NULL--`.
    Check marker reflected verbatim in response, same mechanism as
    `xss_scanner.py`'s reflection check.
  - `time`: `payload_template` plus a `sleep_seconds` number. ONE
    baseline request (unmodified `target_url`) is fetched once per
    `scan()` call, not once per payload -- the endpoint's normal
    response time does not depend on which payload is being tried.
    Flags a candidate if `payload_elapsed - baseline_elapsed >=
    sleep_seconds - TIME_THRESHOLD_SLACK_SECONDS` (a fixed 1-second
    slack, not a percentage -- authored, not cited; a fixed buffer is
    simpler to reason about and test than a ratio, and this is a cheap
    Fast Lane pre-filter, not the 2.5σ-precise verifier).
  - `boolean`: two payload entries sharing a `pair_id`, one
    `boolean_role: "true"` and one `"false"`. Both are requested for
    each injection point; flags a candidate if the two responses'
    `status_code` differs OR their body lengths differ -- a coarse,
    single-pair differential (Section 7.2's OWN precise
    `boolean_differential_confirmed` rule, 5-per-arm with content-
    stability checking, is deliberately NOT reimplemented here; see
    scope note above).

`time_fn` IS CONSTRUCTOR-INJECTABLE, MATCHING `RateLimiter`'s ESTABLISHED
PATTERN (`core/http/rate_limited_client.py`): defaults to
`time.monotonic`, overridable so tests never wait on a real 5-second
`SLEEP()`.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Callable

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "sqli_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

TIME_THRESHOLD_SLACK_SECONDS = 1.0  # authored, not cited -- see module docstring

# Authored, not exhaustive -- a representative sample across the major
# engines this project's own workflows name (Section 7.2's "error-based";
# Section 7.7's Jinja2/Twig/Freemarker list for the SSTI scanner is the
# closest existing precedent for "representative, not exhaustive, set,
# documented as such"). A real dictionary (sqlmap-scale) is out of scope
# for a Fast Lane pre-filter.
DB_ERROR_SIGNATURES: tuple[str, ...] = (
    "sql syntax",  # MySQL: "You have an error in your SQL syntax"
    "mysql_fetch",
    "unclosed quotation mark",  # MSSQL
    "quoted string not properly terminated",  # Oracle
    "sqlite3.operationalerror",
    "sqlite syntax error",
    "postgresql",
    "pg_query",
    "org.postgresql",
    "ora-01756",  # Oracle: quoted string not properly terminated
    "ora-00933",  # Oracle: SQL command not properly ended
)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `sqli_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _make_marker() -> str:
    """See `xss_scanner._make_marker` -- identical reasoning, Section
    7.2's own probe-marker convention ("XBOW_PROBE_12345")."""
    return f"XBOW_PROBE_{secrets.token_hex(8)}"


@register("sqli_scanner")
class SQLiScanner(BaseScanner):
    """Section 7.2. See module docstring for the four techniques and
    their detection rules."""

    def __init__(
        self,
        session,
        *,
        payloads: list[dict] | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`.
            time_fn: Monotonic clock, `() -> float`. Defaults to
                `time.monotonic`; overridable for deterministic tests of
                the `time` technique (module docstring).
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()
        self._time_fn = time_fn if time_fn is not None else time.monotonic

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module and class docstrings.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            See `BaseScanner.scan`.
        """
        candidates: list[ExploitCandidate] = []

        by_technique: dict[str, list[dict]] = {}
        for p in self._payloads:
            by_technique.setdefault(p["technique"], []).append(p)

        candidates += await self._scan_error(target_url, by_technique.get("error", []))
        candidates += await self._scan_union(target_url, by_technique.get("union", []))
        candidates += await self._scan_time(target_url, by_technique.get("time", []))
        candidates += await self._scan_boolean(target_url, by_technique.get("boolean", []))
        return candidates

    async def _scan_error(self, target_url: str, payloads: list[dict]) -> list[ExploitCandidate]:
        found: list[ExploitCandidate] = []
        for payload_entry in payloads:
            payload = payload_entry["payload_template"]
            for point in iter_query_param_injections(target_url, payload):
                response = await self.session.request("GET", point.url)
                body_lower = response.text.lower()
                if any(sig in body_lower for sig in DB_ERROR_SIGNATURES):
                    found.append(
                        self._candidate(target_url, point.parameter, payload, response.text)
                    )
        return found

    async def _scan_union(self, target_url: str, payloads: list[dict]) -> list[ExploitCandidate]:
        found: list[ExploitCandidate] = []
        for payload_entry in payloads:
            marker = _make_marker()
            payload = payload_entry["payload_template"].format(marker=marker)
            for point in iter_query_param_injections(target_url, payload):
                response = await self.session.request("GET", point.url)
                if marker in response.text:
                    found.append(
                        self._candidate(
                            target_url, point.parameter, payload, response.text,
                            probe_correlation_id=None,
                        )
                    )
        return found

    async def _scan_time(self, target_url: str, payloads: list[dict]) -> list[ExploitCandidate]:
        if not payloads or not iter_query_param_injections(target_url, "_probe"):
            # No time payloads, or target_url has no query parameters to
            # inject into at all -- match every other technique's "no
            # params, no requests" behavior; a baseline fetch would
            # otherwise fire even when nothing downstream can use it.
            return []

        baseline_start = self._time_fn()
        await self.session.request("GET", target_url)
        baseline_elapsed = self._time_fn() - baseline_start

        found: list[ExploitCandidate] = []
        for payload_entry in payloads:
            payload = payload_entry["payload_template"]
            sleep_seconds = payload_entry["sleep_seconds"]
            for point in iter_query_param_injections(target_url, payload):
                start = self._time_fn()
                response = await self.session.request("GET", point.url)
                elapsed = self._time_fn() - start

                if elapsed - baseline_elapsed >= sleep_seconds - TIME_THRESHOLD_SLACK_SECONDS:
                    found.append(
                        self._candidate(target_url, point.parameter, payload, response.text)
                    )
        return found

    async def _scan_boolean(self, target_url: str, payloads: list[dict]) -> list[ExploitCandidate]:
        pairs: dict[str, dict[str, dict]] = {}
        for p in payloads:
            pairs.setdefault(p["pair_id"], {})[p["boolean_role"]] = p

        found: list[ExploitCandidate] = []
        for pair_id, roles in pairs.items():
            if "true" not in roles or "false" not in roles:
                continue  # incomplete pair -- nothing to compare, not an error

            true_payload = roles["true"]["payload_template"]
            false_payload = roles["false"]["payload_template"]

            true_points = iter_query_param_injections(target_url, true_payload)
            false_points_by_param = {
                pt.parameter: pt for pt in iter_query_param_injections(target_url, false_payload)
            }

            for true_point in true_points:
                false_point = false_points_by_param.get(true_point.parameter)
                if false_point is None:
                    continue

                true_response = await self.session.request("GET", true_point.url)
                false_response = await self.session.request("GET", false_point.url)

                differs = (
                    true_response.status_code != false_response.status_code
                    or len(true_response.text) != len(false_response.text)
                )
                if differs:
                    found.append(
                        self._candidate(
                            target_url, true_point.parameter, true_payload, true_response.text
                        )
                    )
        return found

    def _candidate(
        self,
        target_url: str,
        parameter: str,
        payload: str,
        response_text: str,
        *,
        probe_correlation_id: str | None = None,
    ) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="sqli",
            endpoint=target_url,
            http_method="GET",
            parameter=parameter,
            detected_by="sqli_scanner",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=probe_correlation_id,
        )
