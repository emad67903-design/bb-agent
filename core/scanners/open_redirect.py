"""
Implements: Section 7.19 -- open_redirect.py
Blueprint: bb_agent_v6.6_final_blueprint.md

BLUEPRINT'S OWN LITERAL EXAMPLE USED VERBATIM (`https://example.com`)
FOR THE STANDARD-URL VARIANT; THREE MORE VARIANTS FROM SECTION 3.1, NOT
JUST SECTION 7.19'S PROSE: `redirect_payloads.json`'s own stub `_notes`
field ("Protocol-relative, javascript:, data:", Section 3.1's table)
names three variant types beyond Section 7.19's single literal
example -- caught by reading the stub before assuming its prose was the
complete payload set. All four (`https://example.com`,
`//xbow-open-redirect-probe.example`, `javascript:void(0)`,
`data:text/plain,...`) use the identical detection check below; only
the payload string differs. `javascript:void(0)` (a no-op) and a
`text/plain` `data:` URI are used rather than anything that would
render or execute -- this scanner only ever inspects the `Location`
response header, never navigates a browser to it, but matching the
project's general safe-by-construction habit costs nothing here.

NOT A RANDOMIZED MARKER -- A DELIBERATE DEPARTURE FROM `xss_scanner.py`/
`host_header.py`'s OWN CONVENTION, WITH REASONING, NOT AN OVERSIGHT:
those scanners randomize their probe value because the check itself
(does *any* trace of the marker appear anywhere in a response body) is
loose enough that a fixed, guessable literal risks coincidentally
matching unrelated page content. This scanner's check is categorically
different: it requires the `Location` response header to point AT the
exact payload just sent, on a genuine redirect status code -- no
legitimate application's `Location` header would coincidentally
redirect to any of these four literal values on its own. A random
marker would add no robustness here, and these are standard,
recognizable, safe choices for exactly this kind of test.

ALL EXISTING QUERY PARAMETERS TESTED, NOT ONLY ONES NAMED `redirect`/
`url`/`next`: Section 7.19's detection text names these three as
examples of common redirect-parameter names, not an exhaustive
allowlist -- a vulnerable app could just as easily use `returnTo`,
`continue`, `goto`, etc. Testing every existing parameter (Batch 1's
established convention via `iter_query_param_injections`) is broader
and catches those too; the response itself (a genuine redirect to the
exact injected URL) is what proves the finding, not the parameter's
name.

BASELINE COMPARISON INCLUDED EVEN THOUGH THE PROBE CHECK IS ALREADY
HIGH-SPECIFICITY: Section 7.19's own evidence line names `differential`
as one of exactly two required types (`replay_stable` + `differential`
= 2), so a baseline fetch is required evidence, not optional
robustness -- one baseline per `scan()` call, same convention every
other scanner in this project uses.

`interactsh_client` -- NOT DECLARED, same reasoning as
`crlf_injection.py`: Section 7.19 describes no OOB technique.

"Auto-feeds `chain_engine`" (Section 7.19) -- OUT OF SCOPE HERE:
`chain_engine.py` (Section 3, Week 5's `ChainExecutionEngine`) consumes
confirmed findings to build multi-hop attack paths; that consumption is
the chain engine's own job, downstream of this scanner emitting a
plain `ExploitCandidate` the same way every other scanner does. Nothing
in this file feeds anything directly -- there is no chain-engine hook
to call from `core/scanners/`.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "redirect_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `redirect_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


@register("open_redirect")
class OpenRedirectScanner(BaseScanner):
    """Section 7.19. See module docstring for the fixed-marker
    reasoning and the baseline-differential requirement."""

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
            (parameter, payload) combination whose response redirected
            to the exact injected URL, differentially from baseline.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []

        baseline = await self.session.request("GET", target_url)
        baseline_location = baseline.headers.get("location")

        candidates: list[ExploitCandidate] = []
        for entry in self._payloads:
            payload = entry["payload"]
            for point in iter_query_param_injections(target_url, payload):
                response = await self.session.request("GET", point.url)
                location = response.headers.get("location")
                if (
                    response.status_code in REDIRECT_STATUS_CODES
                    and location == payload
                    and location != baseline_location
                ):
                    candidates.append(self._candidate(target_url, point.parameter, payload, response.text))
        return candidates

    def _candidate(self, target_url: str, parameter: str, payload: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="open_redirect",
            endpoint=target_url,
            http_method="GET",
            parameter=parameter,
            detected_by="open_redirect",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
