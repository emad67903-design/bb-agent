"""
Implements: Section 7.14 -- oauth_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

REDIRECT_URI MANIPULATION AND TOKEN LEAKAGE ARE ONE PROBE, TWO POSSIBLE
SIGNALS -- READ TOGETHER, NOT AS TWO SEPARATE TECHNIQUES: Section
7.14's "Safe exploit" line ("Insert redirect_uri=https://xbow-probe.com
-> verify code flows to probe URL") describes exactly what "token
leakage" means operationally for this vuln_type -- the code/token
following an accepted, attacker-controlled `redirect_uri`. Each probe
therefore can produce up to two candidates from one response: (1) the
`redirect_uri` was accepted at all, and (2), a strictly stronger
signal, that Location ALSO carries an OAuth response parameter (`code`,
`access_token`, or `token`, checked as real parsed query/fragment KEYS
via `urllib.parse`, not a substring search -- verified directly before
relying on it, confirming a param merely named e.g. `statuscode`
doesn't false-positive on the substring `code`).

ACCEPTANCE CHECK IS `startswith`, NOT EXACT EQUALITY -- CAUGHT BY MY
OWN TEST, NOT ASSUMED CORRECT FROM `open_redirect.py`'S PRECEDENT: a
first draft copied `open_redirect.py`'s `location == payload` exact-
match check. That check is right for `open_redirect.py`, where a bare
redirect is the whole signal, but wrong here: a genuinely leaking OAuth
response NECESSARILY appends `?code=...` or `#access_token=...` to the
injected `redirect_uri`, so `location` can never equal the bare
`payload` exactly in the scenario this scanner most needs to catch --
the exact-match version silently produced zero candidates for the
token-leakage case, the test written to prove leakage detection is
what caught it. Fixed to `location.startswith(payload)`, which admits
appended response parameters while still requiring the injected value
to be the actual authority `location` was built from. Not a
false-positive risk the way a generic prefix check might be elsewhere:
`payload` is this scanner's own just-injected value, not a
attacker-supplied or otherwise untrusted string being matched against
something external.

THREE `redirect_uri` VARIANTS, NOT JUST SECTION 7.14'S ONE LITERAL
EXAMPLE -- AUTHORED, SAME JUSTIFICATION CLASS AS `cmd_injection.py`'S
SEPARATOR x OS VARIANTS: a plain attacker URL
(`https://xbow-oauth-probe.example`, matching the blueprint's own
example shape) tests whether `redirect_uri` validation exists AT ALL.
Two well-documented real-world bypasses of NAIVE prefix/substring
`redirect_uri` validation are also tried: an `@`-trick
(`https://{target_host}@xbow-oauth-probe.example` -- exploits
validators that check "does the string start with the legitimate
host" without accounting for URL authority syntax, where everything
after `@` is the real host) and a subdomain-suffix trick
(`https://xbow-oauth-probe.example/{target_host}` -- exploits
validators that check "does the string CONTAIN the legitimate host"
via a naive substring test). `target_host` is extracted from
`target_url` itself, not guessed.

ONLY THE `redirect_uri` PARAMETER IS TESTED, NOT EVERY EXISTING QUERY
PARAMETER -- A DELIBERATE DEPARTURE FROM `open_redirect.py`'s BROADER
APPROACH: `open_redirect.py` (item 89) tests every parameter because
Section 7.19 names `redirect`/`url`/`next` only as common EXAMPLES, not
an exhaustive convention. OAuth's `redirect_uri` is a STANDARDIZED
parameter name (RFC 6749) -- there is no equivalent naming uncertainty
here, so testing only `redirect_uri` is the more precise, not the
lazier, choice.

STATE ABSENCE ONLY; STATE REUSE NOT ATTEMPTED -- A REAL, FLAGGED GAP,
NOT A SILENT OMISSION: "state absence" is a single-request comparison
(the authorization URL with its own `state` parameter removed, checked
against the unmodified baseline) -- directly testable with the tools
this scanner has. "State reuse" fundamentally requires completing an
authorization flow and replaying the SAME `state` value across a
SECOND, separate attempt -- multi-step flow tracking this project has
no infrastructure for yet (the same test-flow-state gap already flagged
for `jwt_scanner.py`, item 95, and the "Batch 7" scanners, in
miniature). Not attempted with a single-request approximation that
would prove nothing about actual reuse.

`ExploitCandidate.parameter` IS A REAL VALUE FOR BOTH TECHNIQUES, NO
EXTENSION TO ITEM 69'S LIST NEEDED: `redirect_uri` and `state` are both
genuine, named query parameters substituted via `param_injection.py`'s
established pattern -- unlike several of this session's other Batch
3/4 scanners, this one fits Batch 1/2's original shape cleanly.

`interactsh_client` -- NOT DECLARED: Section 7.14 names no OOB
technique.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "oauth_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
OAUTH_RESPONSE_PARAM_KEYS = frozenset({"code", "access_token", "token"})


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `oauth_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _has_oauth_response_params(location_url: str) -> bool:
    """Checks whether `location_url` carries an OAuth response
    parameter as a real, parsed query or fragment key -- see module
    docstring's "REDIRECT_URI MANIPULATION AND TOKEN LEAKAGE" note.

    Args:
        location_url: A `Location` header value.

    Returns:
        `True` if `code`, `access_token`, or `token` appears as an
        actual query or fragment parameter key.
    """
    parsed = urllib.parse.urlsplit(location_url)
    keys = set(urllib.parse.parse_qs(parsed.query)) | set(urllib.parse.parse_qs(parsed.fragment))
    return bool(keys & OAUTH_RESPONSE_PARAM_KEYS)


def _remove_query_param(url: str, key: str) -> str:
    """Returns `url` with `key` removed from its query string entirely
    (not substituted with a value -- `state`'s ABSENCE is the point).

    Args:
        url: The URL to remove a parameter from.
        key: The parameter name to remove.

    Returns:
        `url` with `key` absent from its query string.
    """
    parsed = urllib.parse.urlsplit(url)
    remaining = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k != key]
    new_query = urllib.parse.urlencode(remaining)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


@register("oauth_scanner")
class OAuthScanner(BaseScanner):
    """Section 7.14. See module docstring for the redirect_uri/token-
    leakage combined check and the state-absence-only scope."""

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
            `[]` if `target_url` has no `redirect_uri`/`state`
            parameters to test or no payload entries are loaded.
            Otherwise the concatenation of the redirect_uri/token-
            leakage path's and the state-absence path's candidates.
        """
        if not self._payloads:
            return []

        candidates: list[ExploitCandidate] = []
        candidates.extend(await self._probe_redirect_uri(target_url))
        candidates.extend(await self._probe_state_absence(target_url))
        return candidates

    async def _probe_redirect_uri(self, target_url: str) -> list[ExploitCandidate]:
        """`redirect_uri` variants -- see module docstring's "THREE
        `redirect_uri` VARIANTS" note."""
        points = [pt for pt in iter_query_param_injections(target_url, "_probe") if pt.parameter == "redirect_uri"]
        if not points:
            return []

        target_host = urllib.parse.urlsplit(target_url).hostname or ""
        variant_entries = [p for p in self._payloads if p["technique"] == "redirect_uri_variant"]

        candidates: list[ExploitCandidate] = []
        for entry in variant_entries:
            payload = entry["payload_template"].format(target_host=target_host)
            injected_url = next(
                pt.url for pt in iter_query_param_injections(target_url, payload) if pt.parameter == "redirect_uri"
            )
            response = await self.session.request("GET", injected_url)
            location = response.headers.get("location")

            if (
                response.status_code in REDIRECT_STATUS_CODES
                and location is not None
                and location.startswith(payload)
            ):
                candidates.append(self._candidate(target_url, "redirect_uri", payload, response.text))
                if _has_oauth_response_params(location):
                    candidates.append(self._candidate(target_url, "redirect_uri", payload, response.text))
        return candidates

    async def _probe_state_absence(self, target_url: str) -> list[ExploitCandidate]:
        """`state` parameter removed entirely -- see module docstring's
        "STATE ABSENCE ONLY" note."""
        points = [pt for pt in iter_query_param_injections(target_url, "_probe") if pt.parameter == "state"]
        if not points:
            return []

        baseline = await self.session.request("GET", target_url)
        no_state_url = _remove_query_param(target_url, "state")
        probe = await self.session.request("GET", no_state_url)

        if probe.status_code == baseline.status_code and probe.status_code not in {400, 401, 403}:
            return [self._candidate(target_url, "state", "(removed)", probe.text)]
        return []

    def _candidate(self, target_url: str, parameter: str, payload: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="oauth",
            endpoint=target_url,
            http_method="GET",
            parameter=parameter,
            detected_by="oauth_scanner",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
