"""
Implements: Section 7.17 -- crlf_injection.py
Blueprint: bb_agent_v6.6_final_blueprint.md

RAW CRLF CHARACTERS IN THE STORED PAYLOAD, NOT PRE-PERCENT-ENCODED --
VERIFIED, NOT ASSUMED: `iter_query_param_injections`'s own `urlencode()`
call percent-encodes whatever payload string it's given. Storing the
blueprint's literal example pre-encoded (a literal `"%0d%0aX-XBOW-
PROBE: 1"` string) would be encoded a SECOND time (`%` becomes `%25`
each time), producing a harmless, non-functional payload on the wire.
Confirmed empirically before writing this file: a payload containing a
real `\\r\\n` character correctly becomes exactly one `%0D%0A` in the
constructed URL, matching the blueprint's `%0d%0a` example functionally
(percent-encoding hex digits are case-insensitive per RFC 3986 -- the
case difference is cosmetic, not a defect).

DETECTION IS HEADER-BASED, NOT BODY-BASED: checks
`response.headers.get(probe_header_name) == probe_header_value` -- not
a substring search over `response.text`. A CRLF sequence that only
appears reflected in the response BODY as text is not proof of header
injection; only an actual, distinct response header proves the
underlying HTTP response was really split. The probe header name/value
live in the payload file (config-driven), not hardcoded here.

`interactsh_client` -- NOT DECLARED, NOT AN OMISSION: this scanner has
no OOB path (Section 7.17 describes purely in-band header-injection
detection). Its `__init__` does not take an `interactsh_client`
parameter, matching Batch 1's five scanners' own precedent --
`interactsh_client: InteractshClient | None = None` was pinned (item
83) specifically for the five scanners that need OOB confirmation, not
retrofitted onto every scanner regardless of need.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "crlf_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `crlf_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


@register("crlf_injection")
class CRLFInjectionScanner(BaseScanner):
    """Section 7.17. See module docstring for the encoding note and the
    header-based (not body-based) detection check."""

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
            target_url: See `BaseScanner.scan`.

        Returns:
            `[]` if `target_url` has no query parameters or no payload
            entries are loaded. Otherwise one `ExploitCandidate` per
            (parameter, payload) combination whose probe header was
            reflected in the response.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []

        candidates: list[ExploitCandidate] = []
        for entry in self._payloads:
            payload = entry["payload"]
            for point in iter_query_param_injections(target_url, payload):
                response = await self.session.request("GET", point.url)
                if response.headers.get(entry["probe_header_name"]) == entry["probe_header_value"]:
                    candidates.append(self._candidate(target_url, point.parameter, payload, response.text))
        return candidates

    def _candidate(self, target_url: str, parameter: str, payload: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="crlf_injection",
            endpoint=target_url,
            http_method="GET",
            parameter=parameter,
            detected_by="crlf_injection",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
