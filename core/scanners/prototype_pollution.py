"""
Implements: Section 7.15 -- prototype_pollution.py
Blueprint: bb_agent_v6.6_final_blueprint.md

TWO TECHNIQUES, NOT JUST SECTION 7.15'S ONE LITERAL EXAMPLE --
`prototype_pollution_payloads.json`'s own stub `_notes` field
("`__proto__`/`constructor.prototype` probes", Section 3.1's table)
names both. Section 7.15's prose gives only the `__proto__` example;
`constructor.prototype`-style pollution (`constructor[prototype]
[xbow_probe]=...`) is the other well-known variant for the same class
of vulnerable "deep merge" code, caught by reading the stub before
assuming the prose was the complete payload set.

A NEW INJECTION SHAPE -- ADDING A PARAMETER, NOT SUBSTITUTING ONE:
every scanner built so far substitutes a payload into an EXISTING
query parameter (`param_injection.py`'s pattern) or replaces a whole
body (`xxe_scanner.py`). Section 7.15's payload
(`__proto__[xbow_probe]=12345`) is neither -- it is itself a complete,
new query parameter to be ADDED alongside whatever `target_url` already
has, not a value substituted into one of the existing ones. Kept local
to this file (`_append_query_param`, below) rather than added to
`param_injection.py`: only one scanner in this batch needs it, and this
project's own established practice is not to build shared
infrastructure ahead of a second, proven need (the same discipline
`content_heuristics.py` was itself only shared once a second scanner
genuinely needed it, item 78).

`ExploitCandidate.parameter` = the injected key itself (e.g.
`"__proto__[xbow_probe]"`), NOT `None` -- a genuine judgment call,
considered carefully rather than defaulted: unlike CORS/Host-Header/
CSRF/Auth/XXE (item 69's five no-single-parameter cases, none of which
have ANY query/body parameter in the conventional sense), this
scanner's payload IS structurally a query parameter -- it has a name
and a value and sits in the query string -- it is just a newly-ADDED
one rather than a substituted existing one. That is a difference in
injection MECHANISM, not a difference in whether a parameter exists to
name. Not a sixth no-parameter case.

BEHAVIORAL VERIFICATION, PER SECTION 7.15'S OWN TEXT ("subsequent
response includes polluted key"): two requests per payload -- (1) the
polluted request, adding the payload as a new parameter, (2) a clean
follow-up request to the UNMODIFIED `target_url` (no pollution
parameter at all). A random marker (not the blueprint's literal
`12345`) is checked for in the follow-up response -- if a request that
never included the marker nonetheless returns it, that is direct
evidence of persistent, cross-request state pollution, not a
coincidence a fixed literal like `12345` could not rule out with the
same confidence.

`interactsh_client` -- NOT DECLARED, same reasoning as
`crlf_injection.py`/`open_redirect.py`: Section 7.15 describes no OOB
technique.
"""

from __future__ import annotations

import json
import secrets
import urllib.parse
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "prototype_pollution_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from
    `prototype_pollution_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _append_query_param(url: str, key: str, value: str) -> str:
    """Adds `(key, value)` as an ADDITIONAL query parameter, preserving
    every parameter already on `url` -- see module docstring's "A NEW
    INJECTION SHAPE" note.

    Args:
        url: The URL to add a parameter to.
        key: The new parameter's name (e.g. `"__proto__[xbow_probe]"`).
        value: The new parameter's value.

    Returns:
        `url` with `key=value` appended to its query string.
    """
    parsed = urllib.parse.urlsplit(url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_pairs.append((key, value))
    new_query = urllib.parse.urlencode(query_pairs)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


@register("prototype_pollution")
class PrototypePollutionScanner(BaseScanner):
    """Section 7.15. See module docstring for the two techniques, the
    new append-a-parameter injection shape, and the behavioral
    verification requirement."""

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
            target_url: See `BaseScanner.scan`. No existing-query-
                parameter precondition applies -- see module docstring's
                "A NEW INJECTION SHAPE" note.

        Returns:
            `[]` if no payload entries are loaded. Otherwise one
            `ExploitCandidate` per payload entry whose random marker
            leaked into a clean follow-up request.
        """
        if not self._payloads:
            return []

        candidates: list[ExploitCandidate] = []
        for entry in self._payloads:
            marker = secrets.token_hex(8)
            pollution_key = entry["key_template"]
            polluted_url = _append_query_param(target_url, pollution_key, marker)

            await self.session.request("GET", polluted_url)
            followup = await self.session.request("GET", target_url)

            if marker in followup.text:
                candidates.append(self._candidate(target_url, pollution_key, marker, followup.text))
        return candidates

    def _candidate(self, target_url: str, pollution_key: str, marker: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="prototype_pollution",
            endpoint=target_url,
            http_method="GET",
            parameter=pollution_key,
            detected_by="prototype_pollution",
            payload_used=f"{pollution_key}={marker}",
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
