"""
Implements: Section 7.26 -- mass_assignment.py
Blueprint: bb_agent_v6.6_final_blueprint.md

A FOURTH DISTINCT INJECTION SHAPE -- A WHOLE JSON BODY, LIKE XXE, BUT
WITH A "BASELINE + EXTRA FIELD" STRUCTURE, NOT A FIXED LITERAL: Section
7.26's example (`{"username":"test","isAdmin":true}`) is a complete
JSON body, not a value substituted into an existing parameter --
closer to `xxe_scanner.py`'s whole-body shape than Batch 1/2's
substitution pattern. Unlike XXE's single fixed payload, this scanner
constructs the body itself each time: a baseline field (`username`,
randomized -- see "RANDOMIZED BASELINE FIELD" below) plus one extra,
privilege-shaped field per payload entry.

FOUR PRIVILEGE-ESCALATION FIELD NAMES, NOT JUST SECTION 7.26'S ONE
LITERAL EXAMPLE -- AUTHORED, SAME JUSTIFICATION CLASS AS
`cmd_injection.py`'S SEPARATOR x OS VARIANTS: real mass-assignment
surface varies by which field name a given application actually
exposes internally (`isAdmin`, `is_admin`, `role`, `verified` are all
common real-world conventions for the same class of privileged flag) --
unlike `xxe_scanner.py`/`api_versioning.py`, where Section 7.9/7.24's
technique is singular and extending it would be inventing unrequested
coverage, here the UNCERTAINTY is which naming convention the target
uses, the same uncertainty CMDi's separator/OS variants exist to cover.

RANDOMIZED BASELINE FIELD, NOT THE BLUEPRINT'S LITERAL "test": a fixed
`username: "test"` value risks unwanted side effects on a repeatedly-
scanned real target (duplicate-key/"already exists" errors on a
create-style endpoint) -- `secrets.token_hex` avoids that, the same
practical-safety reasoning already applied to other scanners' markers
(though here for collision-avoidance on the target's own state, not
false-positive avoidance in detection).

DETECTION: PARSE-FIRST, STRING-FALLBACK -- `_field_reflected` attempts
`json.loads` on the response and checks the field's actual value
directly (robust to whitespace/key-ordering); falls back to a
string-based check only if the response isn't valid JSON (an HTML
error page, a non-JSON API) -- not brittle to JSON formatting
variation the way a pure string search would be.

`ExploitCandidate.parameter` = the extra field's name (e.g.
`"isAdmin"`), NOT `None` -- same reasoning as `prototype_pollution.py`
(item 90): the field IS structurally a JSON body field with a name and
a value, just newly-added rather than substituted into something
pre-existing. Not a no-parameter case.

`interactsh_client` -- NOT DECLARED: Section 7.26 names no OOB
technique, same reasoning as every non-OOB scanner in Batches 3/4.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "mass_assignment_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from
    `mass_assignment_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _field_reflected(response_text: str, field_name: str, field_value: object) -> bool:
    """Checks whether `field_name` appears in `response_text` set to
    exactly `field_value` -- see module docstring's "PARSE-FIRST,
    STRING-FALLBACK" note.

    Args:
        response_text: The HTTP response body to check.
        field_name: The injected field's name.
        field_value: The injected field's value.

    Returns:
        `True` if the response reflects the field with that exact value.
    """
    try:
        parsed = json.loads(response_text)
    except (json.JSONDecodeError, ValueError):
        parsed = None

    if isinstance(parsed, dict) and field_name in parsed:
        return parsed[field_name] == field_value

    value_repr = json.dumps(field_value)
    return f'"{field_name}": {value_repr}' in response_text or f'"{field_name}":{value_repr}' in response_text


@register("mass_assignment")
class MassAssignmentScanner(BaseScanner):
    """Section 7.26. See module docstring for the whole-body-with-
    extra-field shape and the four authored field-name variants."""

    def __init__(self, session, *, payloads: list[dict] | None = None) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`. No precondition applies
                -- every candidate URL is tried, matching XXE's
                whole-body technique's endpoint-agnostic approach.

        Returns:
            `[]` if no payload entries are loaded. Otherwise one
            `ExploitCandidate` per field-name entry whose extra field
            was reflected in the probe response but not the baseline.
        """
        if not self._payloads:
            return []

        candidates: list[ExploitCandidate] = []
        for entry in self._payloads:
            field_name = entry["field_name"]
            field_value = entry["field_value"]
            baseline_username = f"xbow_test_{secrets.token_hex(8)}"

            baseline_body = json.dumps({"username": baseline_username})
            probe_body = json.dumps({"username": baseline_username, field_name: field_value})

            baseline_response = await self.session.request(
                "POST", target_url, headers={"Content-Type": "application/json"}, content=baseline_body
            )
            probe_response = await self.session.request(
                "POST", target_url, headers={"Content-Type": "application/json"}, content=probe_body
            )

            if _field_reflected(probe_response.text, field_name, field_value) and not _field_reflected(
                baseline_response.text, field_name, field_value
            ):
                candidates.append(self._candidate(target_url, field_name, probe_body, probe_response.text))
        return candidates

    def _candidate(self, target_url: str, field_name: str, payload: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="mass_assignment",
            endpoint=target_url,
            http_method="POST",
            parameter=field_name,
            detected_by="mass_assignment",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
