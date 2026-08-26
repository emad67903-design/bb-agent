"""
Implements: Section 7.16 -- deserialization.py
Blueprint: bb_agent_v6.6_final_blueprint.md

PURE OOB, NO IN-BAND FALLBACK -- Section 7.16's own text, in full:
"Detection + safe exploit: OOB only. Gadget chain triggers DNS/HTTP to
interactsh. No RCE gadget." Same shape as `cmd_injection.py` (item 84):
when `interactsh_client` is `None` or already `UNAVAILABLE`, `scan()`
returns `[]` entirely -- Section 4.3's "CMDi/XXE/Deser skip OOB phase"
names this scanner explicitly alongside CMDi as having no other phase.

`interactsh_client: InteractshClient | None = None` -- EXACT NAME,
PINNED (docs/DECISIONS.md item 83, Waild's directive): identical
declaration to `ssrf_scanner.py`/`cmd_injection.py`/`xxe_scanner.py`.

`is_allowed_outbound` / `RateLimitedClient` -- SAME CONCLUSION,
RE-TRACED A FOURTH TIME (docs/DECISIONS.md item 86): the gadget's
DNS-lookup target is embedded IN the serialized payload itself,
delivered as a query-parameter value to `target_url`. The target's own
deserialization code path performs the DNS lookup, not this scanner. No
`RateLimitedClient` change needed here either.

LANGUAGE COVERAGE -- PYTHON ONLY. JAVA AND PHP DELIBERATELY NOT
INCLUDED, FLAGGED RATHER THAN FABRICATED (docs/DECISIONS.md item 86 has
the full investigation and reasoning): Section 3.1 describes
`deserialization_payloads.json` as covering "Java/Python/PHP OOB
gadgets." All three were investigated before writing any payload:
  - PYTHON: built and verified end-to-end in this sandbox -- a safe,
    non-RCE `__reduce__` gadget (`socket.gethostbyname(oob_url)` only;
    no file I/O, no subprocess, no code execution), mirroring the
    security community's standard "safest gadget" pattern (Java
    ysoserial's URLDNS: DNS resolution only, nothing else). Round-
    tripped through `pickle.dumps`/`pickletools.dis`/`pickle.loads`
    against a patched `_socket.gethostbyname` across three different
    hostname lengths before being trusted.
  - JAVA: this environment has a JRE (`java`) but no compiler
    (`javac`) -- no way to build AND verify a custom serialization
    gadget here. A pure-JDK equivalent to URLDNS (`java.net.URL`'s
    `hashCode()` performs a DNS lookup during `HashMap`
    deserialization -- no third-party library needed) is the closest
    analog to what was built for Python, but it cannot be constructed
    and verified without a compiler. Deliberately NOT fetching a
    pre-built exploit-generation tool (ysoserial) to work around
    this -- that is a materially different action than hand-authoring
    one narrow, well-documented technique, and out of scope for what
    this scanner needs.
  - PHP: not installed in this environment, and more fundamentally,
    PHP object-injection gadgets are inherently target-library-
    specific (they depend on a particular class with a dangerous
    `__wakeup`/`__destruct`/`__toString` on the TARGET's own
    classpath) -- unlike Python's `__reduce__`, there is no single
    "universal" PHP gadget the way ysoserial/phpggc curate
    library-specific chains for known frameworks. Nothing generic to
    build here without knowing the target's stack in advance.
This scanner covers Python OOB deserialization detection only. Java/PHP
coverage is a real, open gap, not a silent omission -- recorded in
`deserialization_payloads.json`'s own `_status` field and in
DECISIONS.md item 86 for Waild's review, not left for a future reader
to discover by diffing payload counts.

PLACEHOLDER-SUBSTITUTION, NOT `.format(oob_url=...)` -- THE PICKLE
PAYLOAD IS BINARY, STORED BASE64-ENCODED: unlike `ssrf_scanner.py`/
`cmd_injection.py`/`xxe_scanner.py`'s plain-text templates, a pickle
byte stream can't safely use `str.format()` as text. The stored
template embeds a literal placeholder (`PICKLE_OOB_PLACEHOLDER`) baked
into the pickle bytes at authoring time; at scan time,
`_render_pickle_payload` base64-decodes the template, replaces the
placeholder at the BYTES level with the real `oob_url`, then
re-encodes. Safe specifically because pickle protocol 0's `UNICODE`
opcode is newline-terminated, not length-prefixed (confirmed via
`pickletools.dis` before relying on it) -- the substituted string's
length does not need to match the placeholder's. Verified against
three different-length hostnames before this design was accepted.

QUERY-PARAMETER INJECTION, LIKE `cmd_injection.py`, NOT WHOLE-BODY LIKE
`xxe_scanner.py`: the base64-encoded gadget is substituted into an
EXISTING query parameter's value (`param_injection.py`'s established
pattern) -- matches `deserialization_payloads.json`'s own
`injectable_payload` classification (Section 3.1), the same category
CMDi/SSRF use. Real-world insecure deserialization commonly manifests
via a cookie or parameter carrying a serialized blob, so
parameter-substitution fits this vuln_type's actual attack surface,
unlike XXE's fundamentally whole-body mechanism.
`ExploitCandidate.parameter` is a real value here, not `None` --
deserialization is not one of item 69's four no-parameter cases.
"""

from __future__ import annotations

import asyncio
import base64
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

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "deserialization_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention
# Must match the literal baked into every stored payload_template_b64 entry
# at authoring time -- see module docstring's "PLACEHOLDER-SUBSTITUTION" note.
PICKLE_OOB_PLACEHOLDER = "OOB_URL_PLACEHOLDER"

logger = logging.getLogger(__name__)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from
    `deserialization_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _render_pickle_payload(template_b64: str, oob_url: str) -> str:
    """Substitutes `oob_url` for `PICKLE_OOB_PLACEHOLDER` at the bytes
    level inside the base64-decoded pickle template, then re-encodes.

    Args:
        template_b64: The stored template's base64 string, containing
            `PICKLE_OOB_PLACEHOLDER` literally inside the decoded bytes.
        oob_url: The real callback hostname to substitute in.

    Returns:
        A new base64 string, ready to use as a query-parameter value.
    """
    raw = base64.b64decode(template_b64)
    substituted = raw.replace(PICKLE_OOB_PLACEHOLDER.encode("ascii"), oob_url.encode("ascii"))
    return base64.b64encode(substituted).decode("ascii")


@register("deserialization")
class DeserializationScanner(BaseScanner):
    """Section 7.16. See module docstring for the Python-only gadget
    coverage and the placeholder-substitution payload mechanism."""

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
                (default) means `scan()` returns `[]` -- this scanner
                has no non-OOB path, same as `cmd_injection.py`.
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
            gadget) combination whose OOB callback was received.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []
        if self._interactsh_client is None or self._interactsh_client.mode is InteractshMode.UNAVAILABLE:
            logger.info(
                "[DESERIALIZATION_OOB_UNAVAILABLE] %s -- no OOB path, scan skipped (Section 7.16: OOB only)",
                target_url,
            )
            return []

        param_names = [pt.parameter for pt in iter_query_param_injections(target_url, "_probe")]

        probes: list[tuple[str, str, str, str]] = []  # (parameter, payload, oob_url, injected_url)
        for param_name in param_names:
            for entry in self._payloads:
                oob_url = self._interactsh_client.register_probe()
                payload = _render_pickle_payload(entry["payload_template_b64"], oob_url)
                injected_url = next(
                    pt.url
                    for pt in iter_query_param_injections(target_url, payload)
                    if pt.parameter == param_name
                )
                probes.append((param_name, payload, oob_url, injected_url))

        response_texts: dict[tuple[str, str], str] = {}
        for param_name, _payload, oob_url, injected_url in probes:
            response = await self.session.request("GET", injected_url)
            response_texts[(param_name, oob_url)] = response.text

        outcomes = await asyncio.gather(*(self._interactsh_client.poll(oob_url) for _p, _pl, oob_url, _u in probes))

        candidates: list[ExploitCandidate] = []
        for (param_name, payload, oob_url, _injected_url), outcome in zip(probes, outcomes):
            if outcome is OOBPollOutcome.RECEIVED:
                correlation_id = oob_url.removesuffix(INTERACTSH_SUFFIX)
                candidates.append(
                    ExploitCandidate(
                        vuln_type="deserialization",
                        endpoint=target_url,
                        http_method="GET",
                        parameter=param_name,
                        detected_by="deserialization",
                        payload_used=payload,
                        raw_response_snapshot=response_texts[(param_name, oob_url)][:RAW_RESPONSE_SNAPSHOT_CHARS],
                        probe_correlation_id=correlation_id,
                    )
                )
        return candidates
