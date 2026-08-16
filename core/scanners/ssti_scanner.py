"""
Implements: Section 7.7 -- ssti_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE -- EXPRESSION EVALUATION ONLY: Section 7.7 is explicit and, unlike
XSS/SQLi, does not defer anything to a not-yet-built verifier: "Safe
exploit: Expression evaluation only. No file read, no RCE. Engine
fingerprint = proof." This scanner's detection IS the safe exploit --
there is no separate confirmation step this batch defers, unlike
`xss_scanner.py`/`sqli_scanner.py`'s explicit scope notes. Section 7.7's
own Tier C note reinforces the boundary from the other direction: "Any
action beyond expression evaluation requires human approval" -- this
scanner never attempts anything beyond `{{7*7}}`-style arithmetic.

BASELINE COMPARISON IS REQUIRED, UNLIKE XSS/SQLi's UNION TECHNIQUE:
`expected` values ("49") are short, plausible, and NOT random per-probe
markers -- a page that happens to already contain "49" for unrelated
reasons (a price, an ID, a year fragment) would otherwise false-positive.
One baseline (unmodified `target_url`) is fetched once per `scan()` call
-- the same "one baseline, not one per payload" optimization
`sqli_scanner.py`'s `time` technique already uses, for the same reason
(baseline content does not depend on which payload is about to be
tried). A candidate fires only if `expected` is present in the payload
response AND absent from the baseline -- pure `differential`.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "ssti_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `ssti_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


@register("ssti_scanner")
class SSTIScanner(BaseScanner):
    """Section 7.7. See module docstring for the baseline-differential
    detection design."""

    def __init__(self, session, *, payloads: list[dict] | None = None) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring. One `ExploitCandidate` per (query
        parameter, engine payload) combination where `expected` appears
        in the payload response but not in the unmodified baseline.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            See `BaseScanner.scan`.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []

        baseline = await self.session.request("GET", target_url)

        candidates: list[ExploitCandidate] = []
        for payload_entry in self._payloads:
            payload = payload_entry["payload"]
            expected = payload_entry["expected"]

            if expected in baseline.text:
                # This engine's expected marker already occurs on the
                # unmodified page -- differential can never fire cleanly
                # for it here, so skip rather than risk a false positive.
                continue

            for point in iter_query_param_injections(target_url, payload):
                response = await self.session.request("GET", point.url)
                if expected in response.text:
                    candidates.append(
                        ExploitCandidate(
                            vuln_type="ssti",
                            endpoint=target_url,
                            http_method="GET",
                            parameter=point.parameter,
                            detected_by="ssti_scanner",
                            payload_used=payload,
                            raw_response_snapshot=response.text[:RAW_RESPONSE_SNAPSHOT_CHARS],
                        )
                    )
        return candidates
