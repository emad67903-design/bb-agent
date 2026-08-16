"""
Implements: Section 7.1 -- xss_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE -- REFLECTION DETECTION ONLY, NOT DOM EXECUTION: Section 7.1's
evidence for a reportable finding is `replay_stable` + `differential` +
`variant_confirmed` = 3 (meets the Section 5.4 minimum on its own);
`dom_execution_confirmed` is an explicitly optional 4th type, "collected
... when the console probe fires" -- i.e. when Playwright actually
navigates the page and captures a `console.log`. That requires a real
browser (`core/browser/browser_tool.py`, Week 3) and is the Safe Exploit
Harness's job (Section 7.1: "Safe exploit: <img src=x
onerror=console.log('XBOW_XSS_12345')> -> Playwright console capture"),
not this scanner's. `core/verifier/xss_verifier.py` does not exist yet
(grep-confirmed before this file was written) -- consistent with that
being a separate, not-yet-built pipeline stage. This scanner's job,
Fast Lane's job generally (Section 6.6: "0 LLM calls," cheap and broad):
detect UNESCAPED REFLECTION of a unique marker via plain HTTP, which is
sufficient on its own to raise a candidate (none of the three
minimum-meeting evidence types require DOM execution).

DETECTION MECHANISM: for each of the payload file's 6 templates (3
contexts: html_body, html_attribute, js_string -- Section 7.1: "HTML
body, attributes, JS context"), resolve `{marker}` to a fresh, per-probe
random token (not the blueprint's literal `XBOW_XSS_12345` -- a fixed
literal would let one page's cached/logged response falsely "confirm" a
completely different probe; Section 7.1's `12345` is illustrative, not a
literal requirement, and Section 4.2's `InteractshClient` already
establishes this codebase's convention of randomized-suffix markers for
exactly this reason). No baseline comparison is needed the way SSTI/LFI
need one (their expected strings, "49" or hostname-shaped content, can
occur by coincidence; a fresh random 16-hex-char marker reflected
verbatim cannot).

DOM SINKS (`innerHTML`, `document.write`, `eval` -- Section 7.1) ARE NOT
TESTED BY THIS SCANNER: detecting a DOM-sink vulnerability, as opposed to
a plain HTML/attribute/JS-string reflection, means the payload reaches a
sink via client-side JavaScript rather than server-side response
rendering -- an HTTP-only, no-browser scanner cannot observe that
distinction. Flagged here as a real, not silently dropped, gap in this
batch's coverage: static reflection is tested; DOM-sink-specific
behavior is not.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "xss_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention (docs/DECISIONS.md item 69)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `xss_payloads.json`.

    Args:
        path: Payload file path. Overridable for tests.

    Returns:
        The raw list of `{id, context, payload_template}` dicts.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _make_marker() -> str:
    """A fresh, unguessable per-probe marker. See module docstring for
    why this must be random, not the blueprint's literal `XBOW_XSS_12345`."""
    return f"XBOW_XSS_{secrets.token_hex(8)}"


@register("xss_scanner")
class XSSScanner(BaseScanner):
    """Section 7.1. See module docstring for scope and detection design."""

    def __init__(self, session, *, payloads: list[dict] | None = None) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: Overrides the payload file's content -- test
                injection point; production callers omit this and get
                `xss_payloads.json`'s real content via `_load_payloads()`.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring. One `ExploitCandidate` per (query
        parameter, payload) combination where the marker-resolved
        payload is found reflected verbatim in the response body.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            See `BaseScanner.scan`.
        """
        candidates: list[ExploitCandidate] = []

        for payload_entry in self._payloads:
            marker = _make_marker()
            resolved_payload = payload_entry["payload_template"].format(marker=marker)

            for point in iter_query_param_injections(target_url, resolved_payload):
                response = await self.session.request("GET", point.url)
                if resolved_payload in response.text:
                    candidates.append(
                        ExploitCandidate(
                            vuln_type="xss",
                            endpoint=target_url,
                            http_method="GET",
                            parameter=point.parameter,
                            detected_by="xss_scanner",
                            payload_used=resolved_payload,
                            raw_response_snapshot=response.text[:RAW_RESPONSE_SNAPSHOT_CHARS],
                        )
                    )

        return candidates
