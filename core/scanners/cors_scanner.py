"""
Implements: Section 7.11 -- cors_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

NO PAYLOAD FILE, BY DESIGN -- Section 3.1'S OWN NOTE: "Both
[`auth_scanner.py` and `cors_scanner.py`] use header-level and
state-machine analysis, not injected payloads. No payload file by
design." This scanner's `__init__` takes no `payloads` parameter and
there is no `_load_payloads()`/`PAYLOAD_FILE` in this file -- headers
are constructed dynamically (Section 7.11's own text). Confirmed: no
`cors_payloads.json` exists in `data/payloads/`, nor is one named in
Section 3.1's 26-file table.

`ExploitCandidate.parameter = None` -- ALREADY CORRECTLY ANTICIPATED BY
ITEM 69, CONFIRMED HERE, NOT A NEW FINDING (same shape as
`host_header.py`, item 87): item 69's docstring names CORS as one of
its original four no-single-parameter `vuln_type`s ("CORS (Origin
header, not a query/body parameter)"). This scanner injects via the
`Origin` HTTP header, never a query/body parameter.

FOUR INDEPENDENT SIGNALS, MATCHING SECTION 7.11'S TEXT EXACTLY -- each
its own check, each capable of emitting its own candidate (0 to 4
candidates possible per `scan()` call; Section 5.4's own note that
CORS's `signal_min` was deliberately raised from 2 to 4, "same
rationale as xss's elevated bar," for high-false-positive reflection
findings -- this scanner's job is to emit signals honestly, not to
enforce that threshold itself; threshold enforcement is downstream):
  1. ORIGIN REFLECTION: `Access-Control-Allow-Origin` echoes a random,
     unique probe `Origin` back exactly -- a fixed literal like the
     blueprint's own "evil.com" example risks a coincidental match
     against some unrelated static ACAO value; random avoids that, the
     same reasoning `host_header.py` already established for its own
     marker.
  2. ACAC:true ALONGSIDE REFLECTION -- a STRICTER version of signal 1,
     not a standalone check: only meaningful (and only genuinely
     dangerous, per Section 7.11's own "Safe exploit: Origin reflection
     + ACAC confirmed") in combination with a reflected origin. Fires
     as a second, additional candidate on the SAME response signal 1
     already confirmed, when `Access-Control-Allow-Credentials` is also
     `"true"`.
  3. PREFLIGHT BYPASS: an `OPTIONS` request
     (`Access-Control-Request-Method: PUT`) with the same random probe
     `Origin` -- checks whether the preflight response itself grants
     access (reflects the origin AND allows the requested method),
     which real browsers rely on to decide whether to send the actual
     cross-origin request at all.
  4. NULL ORIGIN: `Origin: null` (the literal string browsers send from
     sandboxed contexts -- `data:` URLs, sandboxed iframes) -- not
     randomizable, since the test is specifically whether the literal
     word `"null"` is accepted.

DEDUP_KEY GRANULARITY -- A REAL, FLAGGED LIMITATION, NOT WORKED AROUND:
`DEDUP_KEY = (vuln_type, endpoint_path, http_method, parameter)`.
Signals 1, 2, and 4 all use `http_method="GET"` and
`parameter=None` (unavoidable -- CORS has no parameter to name, per
item 69, above) -- meaning three of CORS's four signals share an
identical dedup key on a given endpoint and would collapse under
Section 6.9's dedup policy (only signal 3's `"OPTIONS"` method
distinguishes it). Considered encoding the signal name into
`parameter` purely to force distinctness, and rejected: that would
misuse a field item 69 already established has no genuine value here,
contradicting a previously-confirmed convention to work around a
reporting-granularity limitation. Section 6.9's own dedup policy merges
same-key findings into one with richer evidence rather than discarding
information, so the practical impact is reduced distinctness in
reporting, not lost detection -- a real, worth-flagging gap in the
current ontology, not a correctness bug. See docs/DECISIONS.md item 91
(this scanner's own build entry).

`interactsh_client` -- NOT DECLARED, same reasoning as every other
non-OOB scanner in this batch.
"""

from __future__ import annotations

import secrets

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention


@register("cors_scanner")
class CORSScanner(BaseScanner):
    """Section 7.11. See module docstring for the four independent
    signals and the no-payload-file design."""

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`. No precondition applies
                -- every candidate URL is tested, matching CORS's
                header-only, endpoint-agnostic technique.

        Returns:
            0 to 4 `ExploitCandidate`s, one per signal that fired.
        """
        candidates: list[ExploitCandidate] = []
        probe_origin = f"https://xbow-cors-{secrets.token_hex(8)}.example"

        response = await self.session.request("GET", target_url, headers={"Origin": probe_origin})
        acao = response.headers.get("access-control-allow-origin")
        acac = response.headers.get("access-control-allow-credentials")
        if acao == probe_origin:
            candidates.append(self._candidate(target_url, "GET", probe_origin, response.text))
            if acac == "true":
                candidates.append(self._candidate(target_url, "GET", probe_origin, response.text))

        preflight_response = await self.session.request(
            "OPTIONS",
            target_url,
            headers={"Origin": probe_origin, "Access-Control-Request-Method": "PUT"},
        )
        preflight_acao = preflight_response.headers.get("access-control-allow-origin")
        preflight_methods = preflight_response.headers.get("access-control-allow-methods", "")
        if preflight_acao == probe_origin and "PUT" in preflight_methods.upper():
            candidates.append(self._candidate(target_url, "OPTIONS", probe_origin, preflight_response.text))

        null_response = await self.session.request("GET", target_url, headers={"Origin": "null"})
        null_acao = null_response.headers.get("access-control-allow-origin")
        if null_acao == "null":
            candidates.append(self._candidate(target_url, "GET", "null", null_response.text))

        return candidates

    def _candidate(self, target_url: str, http_method: str, origin_used: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="cors",
            endpoint=target_url,
            http_method=http_method,
            parameter=None,
            detected_by="cors_scanner",
            payload_used=f"Origin: {origin_used}",
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
