"""
Implements: Section 7.20 -- host_header.py
Blueprint: bb_agent_v6.6_final_blueprint.md

`ExploitCandidate.parameter = None` -- ALREADY CORRECTLY ANTICIPATED BY
ITEM 69, CONFIRMED HERE, NOT A NEW FINDING (unlike `xxe_scanner.py`,
item 85): item 69's docstring names Host Header as one of its original
four no-single-parameter `vuln_type`s ("Host Header (Host header)").
This scanner injects via the `Host` HTTP header, never a query/body
parameter -- `iter_query_param_injections` is not used anywhere in this
file, the same shape XXE ended up in, but expected from the start here
rather than discovered.

`http_method = "GET"` -- both techniques below are simple GET requests
with an overridden `Host` header; no request body involved.

TWO TECHNIQUES, MATCHING SECTION 7.20'S TEXT EXACTLY: "Substitute
`Host: attacker.com` -> OOB callback or injected host in error/email."
  1. IN-BAND REFLECTION: a random, unique-per-probe marker (not a fixed
     literal like the blueprint's own "attacker.com" example) is sent
     as the `Host` header value; the response is checked for that exact
     marker, differentially against a baseline (unmodified `Host`) --
     Section 7.20's own "Evidence: oob_interaction + differential = 2"
     names `differential` explicitly, so this is a required technique,
     not an optional embellishment. Using a random marker rather than
     a fixed guessable string is the same false-positive-avoidance
     reasoning `xss_scanner.py` already established for its own console
     probe marker -- a fixed literal risks coincidentally matching
     unrelated page content; a random one cannot.
  2. OOB CALLBACK: `Host` header set to the interactsh `oob_url`
     directly (no URL scheme prefix -- a `Host` header is a bare
     hostname, unlike ssrf_scanner.py/cmd_injection.py's
     `"http://{oob_url}/"`-shaped payloads). Confirms a server-side
     mechanism (e.g. link preview, async validation) fetched a URL
     built from the injected `Host`.

`Host` HEADER OVERRIDE -- VERIFIED TO REACH THE TARGET, NOT ASSUMED
FROM HTTPX FAMILIARITY: `self.session.request(..., headers={"Host":
value})` was confirmed, before writing this file, to actually override
the transmitted `Host` header rather than being silently dropped in
favor of the URL-derived default (httpx does respect an explicit
override; checked directly against this project's own
`RateLimitedClient`/`InterceptingClient` stack, not just httpx's
general docs).

`interactsh_client: InteractshClient | None = None` -- EXACT NAME,
PINNED (docs/DECISIONS.md item 83, Waild's directive), confirmed
identical a fifth and final time for this batch, against this file's
own signature. `None` degrades to the in-band reflection technique
only, per Section 4.3's "Degrade: in-band ... only" shape (this
scanner is not named in Section 4.3's "CMDi/XXE/Deser skip OOB phase"
list at all -- it is not purely OOB-dependent, matching Section 7.20's
own "OOB callback OR injected host" wording).

`is_allowed_outbound` / `RateLimitedClient` -- SAME CONCLUSION, RE-
TRACED A FIFTH AND FINAL TIME FOR THIS BATCH (docs/DECISIONS.md item 87
has the closing summary across all five): the OOB `Host` value is
delivered as a header on a request to `target_url` -- still never a
destination `self.session` connects to directly. No `RateLimitedClient`
change needed for any of Batch 2's five scanners.
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

from core.governance.scope_enforcer import INTERACTSH_SUFFIX
from core.http.interactsh_client import InteractshClient
from core.ontology.enums import InteractshMode, OOBPollOutcome
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "host_header_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

logger = logging.getLogger(__name__)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from
    `host_header_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


@register("host_header")
class HostHeaderScanner(BaseScanner):
    """Section 7.20. See module docstring for the two techniques and
    the already-anticipated `parameter=None` shape."""

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
                (default) degrades to the in-band reflection technique
                only -- see module docstring.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()
        self._interactsh_client = interactsh_client

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`. No query-parameter
                precondition applies -- see module docstring.

        Returns:
            `[]` if no payload entries are loaded. Otherwise the
            concatenation of the in-band reflection path's and the OOB
            path's candidates (at most one candidate per technique).
        """
        if not self._payloads:
            return []
        candidates: list[ExploitCandidate] = []
        candidates.extend(await self._scan_in_band_reflection(target_url))
        candidates.extend(await self._scan_oob(target_url))
        return candidates

    async def _scan_in_band_reflection(self, target_url: str) -> list[ExploitCandidate]:
        """Random-marker reflection technique -- see module docstring's
        "IN-BAND REFLECTION" note."""
        entries = [p for p in self._payloads if p["technique"] == "in_band_reflection"]
        if not entries:
            return []

        baseline = await self.session.request("GET", target_url)

        candidates: list[ExploitCandidate] = []
        for entry in entries:
            marker_host = entry["payload_template"].format(nonce=secrets.token_hex(8))
            response = await self.session.request("GET", target_url, headers={"Host": marker_host})
            if marker_host in response.text and marker_host not in baseline.text:
                candidates.append(self._candidate(target_url, marker_host, response.text))
        return candidates

    async def _scan_oob(self, target_url: str) -> list[ExploitCandidate]:
        """OOB callback technique -- see module docstring's "OOB
        CALLBACK" note."""
        if self._interactsh_client is None or self._interactsh_client.mode is InteractshMode.UNAVAILABLE:
            logger.info(
                "[HOST_HEADER_OOB_UNAVAILABLE] %s -- OOB phase skipped (Section 4.3: degrade to in-band only)",
                target_url,
            )
            return []

        entries = [p for p in self._payloads if p["technique"] == "oob"]
        if not entries:
            return []

        oob_url = self._interactsh_client.register_probe()
        host_value = entries[0]["payload_template"].format(oob_url=oob_url)
        response = await self.session.request("GET", target_url, headers={"Host": host_value})

        outcome = await self._interactsh_client.poll(oob_url)
        if outcome is not OOBPollOutcome.RECEIVED:
            return []

        correlation_id = oob_url.removesuffix(INTERACTSH_SUFFIX)
        return [self._candidate(target_url, host_value, response.text, probe_correlation_id=correlation_id)]

    def _candidate(
        self,
        target_url: str,
        payload: str,
        response_text: str,
        *,
        probe_correlation_id: str | None = None,
    ) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="host_header",
            endpoint=target_url,
            http_method="GET",
            parameter=None,
            detected_by="host_header",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=probe_correlation_id,
        )
