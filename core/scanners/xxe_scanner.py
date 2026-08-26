"""
Implements: Section 7.9 -- xxe_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

WHOLE-XML-BODY TECHNIQUE, NOT QUERY-PARAMETER SUBSTITUTION -- THE FIRST
REAL DEPARTURE FROM `param_injection.py`'S PATTERN: every scanner built
so far (Batch 1, plus `ssrf_scanner.py`/`cmd_injection.py`) works by
substituting a payload into one of `target_url`'s EXISTING query
parameters. XXE has no existing parameter to substitute into -- the
entire POST body IS the payload (a crafted XML document with an
external-entity DTD), sent to `target_url` directly, `Content-Type:
application/xml`. `iter_query_param_injections` is not used anywhere in
this file.

`ExploitCandidate.parameter = None` FOR EVERY XXE CANDIDATE -- EXTENDS
ITEM 69'S FOUR-CASE LIST TO FIVE, FOUND HERE, NOT SILENTLY ASSUMED (docs/
DECISIONS.md item 85 has the full reasoning): item 69's own docstring
names exactly four no-single-parameter vuln_types -- CORS (Origin
header), Host Header (Host header), CSRF (whole-form action), Auth
(state-machine test name) -- and states "every other vuln_type is
expected to supply a real value." XXE was not one of the four. Checked
against XXE's actual mechanics before writing this file: whole-body
injection, no existing parameter, matching CORS/CSRF's own "no single
query/body parameter in the conventional sense" justification more
closely than any of item 69's four originals. `parameter=None` is
correct here too -- flagged explicitly as a finding, not silently
reached for.

`http_method="POST"`, NOT `"GET"` -- XXE's real mechanism is inherently
a POST with an XML body; Section 6.9's `DEDUP_KEY` exists precisely so
GET/POST on the same endpoint don't collide, and this is the accurate
value for what this scanner actually sends, not a copy of Batch 1's GET
convention.

TWO TECHNIQUES, MATCHING SECTION 7.9'S OWN TEXT EXACTLY, NO EXTRA
VARIANTS INVENTED: "OOB DTD callback AND/OR `/etc/hostname` file read."
Unlike `ssrf_scanner.py`/`cmd_injection.py`'s multi-variant payload
sets (authored extensions beyond one blueprint example, justified there
by real per-context/per-OS coverage gaps), Section 7.9 names exactly
two techniques and this file implements exactly two payload entries --
no additional structural DTD variants, no multiple Content-Type
guesses. One reasonable Content-Type (`application/xml`) is used, not
exhaustively covering every content-type a real target might require --
an authored, single-value choice, same "authored, not
blueprint-enumerated" flag every other scanner's own signatures carry.

HOSTNAME CONTENT CHECK REUSES `content_heuristics.looks_like_hostname_
content` -- THIRD SCANNER TO SHARE IT (after `lfi_scanner.py` and
`path_traversal.py`, item 78's note): same three-part rule, same
differential-against-baseline requirement, same inherent black-box
limits (that module's own docstring already states them; not
re-litigated here). Baseline is a GET to `target_url` (unmodified) --
the same one-baseline-per-`scan()` convention every other scanner uses,
even though the real probe is a POST; the baseline's purpose is "what
does this app's normal traffic look like," which a GET represents as
well as anything else would.

`interactsh_client: InteractshClient | None = None` -- EXACT NAME,
PINNED (item 83, Waild's directive): identical declaration, confirmed
against this file's own signature, not re-asserted from silence.
Unlike `cmd_injection.py`, this scanner has a non-OOB fallback (the
hostname file-read technique, per Section 7.9's "AND/OR"), so
`interactsh_client=None` degrades to hostname-only, not to `[]`
entirely -- the same "Degrade: in-band ... only" shape `ssrf_scanner.py`
has, not `cmd_injection.py`'s "no other phase" shape.

`is_allowed_outbound` / `RateLimitedClient` -- SAME CONCLUSION,
RE-TRACED AGAINST THIS SCANNER'S OWN MECHANICS (docs/DECISIONS.md item
85 has the full re-trace, same discipline item 84 applied for
`cmd_injection.py`): the OOB URL lives inside the XML body (an entity
`SYSTEM` identifier), sent to `target_url` -- still not a destination
`self.session` connects to. No `RateLimitedClient` change needed here
either.

ONE `oob_url` PER `scan()` CALL, NOT PER PARAMETER -- there is no
parameter to iterate over (see above), so unlike `ssrf_scanner.py`/
`cmd_injection.py`'s per-(parameter[, variant]) probe loops, this
scanner registers exactly one correlation ID per `scan()` call.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.governance.scope_enforcer import INTERACTSH_SUFFIX
from core.http.interactsh_client import InteractshClient
from core.ontology.enums import InteractshMode, OOBPollOutcome
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.content_heuristics import looks_like_hostname_content
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "xxe_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention
XML_CONTENT_TYPE = "application/xml"  # Authored, single reasonable choice -- see module docstring.

logger = logging.getLogger(__name__)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `xxe_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


@register("xxe_scanner")
class XXEScanner(BaseScanner):
    """Section 7.9. See module docstring for the whole-body technique,
    the `parameter=None` finding, and the OOB/in-band split."""

    def __init__(
        self,
        session,
        *,
        payloads: list[dict] | None = None,
        interactsh_client: InteractshClient | None = None,
    ) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`.
            interactsh_client: See `ssrf_scanner.SSRFScanner`. `None`
                (default) degrades to the in-band hostname-read
                technique only -- see module docstring.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()
        self._interactsh_client = interactsh_client

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`. Unlike every other
                scanner built so far, no query-parameter precondition
                applies here -- see module docstring's "WHOLE-XML-BODY
                TECHNIQUE" note.

        Returns:
            `[]` if no payload entries are loaded. Otherwise the
            concatenation of the in-band hostname-read path's and the
            OOB path's candidates (at most one candidate per technique,
            since neither loops over parameters).
        """
        if not self._payloads:
            return []
        candidates: list[ExploitCandidate] = []
        candidates.extend(await self._scan_in_band_hostname(target_url))
        candidates.extend(await self._scan_oob(target_url))
        return candidates

    async def _scan_in_band_hostname(self, target_url: str) -> list[ExploitCandidate]:
        """`/etc/hostname` file-read technique -- see module docstring's
        "HOSTNAME CONTENT CHECK REUSES" note."""
        entries = [p for p in self._payloads if p["technique"] == "in_band_file_read"]
        if not entries:
            return []

        baseline = await self.session.request("GET", target_url)

        candidates: list[ExploitCandidate] = []
        for entry in entries:
            payload = entry["payload"]
            response = await self.session.request(
                "POST",
                target_url,
                headers={"Content-Type": XML_CONTENT_TYPE},
                content=payload,
            )
            if looks_like_hostname_content(response.text, baseline.text):
                candidates.append(self._candidate(target_url, payload, response.text))
        return candidates

    async def _scan_oob(self, target_url: str) -> list[ExploitCandidate]:
        """OOB DTD callback technique -- see module docstring's "ONE
        `oob_url` PER `scan()` CALL" note."""
        if self._interactsh_client is None or self._interactsh_client.mode is InteractshMode.UNAVAILABLE:
            logger.info(
                "[XXE_OOB_UNAVAILABLE] %s -- OOB phase skipped (Section 4.3: degrade to in-band only)",
                target_url,
            )
            return []

        entries = [p for p in self._payloads if p["technique"] == "oob"]
        if not entries:
            return []

        oob_url = self._interactsh_client.register_probe()
        payload = entries[0]["payload_template"].format(oob_url=oob_url)
        response = await self.session.request(
            "POST",
            target_url,
            headers={"Content-Type": XML_CONTENT_TYPE},
            content=payload,
        )

        outcome = await self._interactsh_client.poll(oob_url)
        if outcome is not OOBPollOutcome.RECEIVED:
            return []

        correlation_id = oob_url.removesuffix(INTERACTSH_SUFFIX)
        return [self._candidate(target_url, payload, response.text, probe_correlation_id=correlation_id)]

    def _candidate(
        self,
        target_url: str,
        payload: str,
        response_text: str,
        *,
        probe_correlation_id: str | None = None,
    ) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="xxe",
            endpoint=target_url,
            http_method="POST",
            parameter=None,
            detected_by="xxe_scanner",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=probe_correlation_id,
        )
