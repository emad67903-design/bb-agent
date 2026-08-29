"""
Implements: Section 7.24 -- api_versioning.py
Blueprint: bb_agent_v6.6_final_blueprint.md

A THIRD DISTINCT INJECTION SHAPE THIS PROJECT HAS NOW BUILT -- neither
query-parameter substitution (Batch 1/2, `crlf_injection.py`/
`open_redirect.py`), nor a newly-added parameter
(`prototype_pollution.py`, this batch), nor a whole-body replacement
(`xxe_scanner.py`). This scanner rewrites a URL PATH SEGMENT: it finds
a version marker in `target_url`'s path (via a regex pattern read from
`api_versioning_payloads.json`, not hardcoded -- see "PATTERN LIVES IN
THE PAYLOAD FILE" below) and constructs an alternate URL with the
version number decremented by one, comparing the two responses.
`iter_query_param_injections` is not used anywhere in this file --
there is no query parameter involved at all.

PATTERN LIVES IN THE PAYLOAD FILE, NOT A HARDCODED PYTHON CONSTANT --
CAUGHT AND FIXED BEFORE THIS FILE WAS DONE, NOT DESIGNED THIS WAY FROM
THE START: `api_versioning_payloads.json` is classified
`injectable_payload` in Section 3.1 (unlike `cors_scanner.py`/
`auth_scanner.py`, which Section 3.1 explicitly exempts from having a
payload file at all). A first draft of this scanner accepted a
`payloads` constructor parameter that `scan()` never actually read --
noticed while reviewing the file, not after: a payload file the
blueprint classifies as real but a scanner that never consumes it is
exactly the kind of silent inconsistency this project's `_status`-field
discipline exists to prevent. The version-segment regex
(`/v(\\d+)/`) now lives in the payload file's own `pattern` field and is
read via `self._payloads` -- config-driven, matching the Engineering
Constitution's "zero magic numbers" mandate, and genuinely exercised,
not vestigial. One pattern entry, not several: `/v(\\d+)/` is a
substring search, not anchored to the path's start, so it already
matches both `/v2/admin` and `/api/v2/admin`-shaped URLs without a
second pattern -- verified before deciding a single entry was
sufficient, not assumed.

PRECONDITION: NO VERSION SEGMENT, OR VERSION 0, MEANS NOTHING TO TEST --
`_find_lower_version_url` returns `None` in both cases and `scan()`
returns `[]` immediately, the same "genuine precondition, not every
scanner's job to apply everywhere" shape `param_injection.py`'s
existing-query-parameter check already established, just for a
different precondition.

ONE VERSION STEP DOWN ONLY, NOT AN EXHAUSTIVE SWEEP: Section 7.24's
text gives one example (`/v1/` bypassing `/v2/`'s denial). This tests
exactly `/v{N-1}/` against the given `/v{N}/`, not every lower version
down to `/v0/` -- the same "don't invent unrequested variants beyond
what the blueprint actually names" discipline `xxe_scanner.py` (item
85) already applied, in contrast to `ssrf_scanner.py`/
`cmd_injection.py`'s deliberately-broader, explicitly-justified variant
sets.

DETECTION LOGIC MATCHES SECTION 7.24'S TEXT EXACTLY: "Access `/v1/
admin` with user session **when** `/v2/admin` returns 403." Read as a
two-part condition, not one: (1) the GIVEN URL (`target_url`) must
itself return 403 first -- if it does not, there is nothing to bypass,
and testing the alternate would prove nothing about access control. (2)
Only if (1) holds does the alternate (lower) version's response get
checked; a non-403 there is the actual signal.

`ExploitCandidate.parameter = None` -- EXTENDS ITEM 69'S LIST TO SIX,
A SECOND SUCH FINDING THIS SESSION (after `xxe_scanner.py`, item 85),
FOUND HERE, NOT ASSUMED: this technique touches a URL PATH SEGMENT, not
a query/body parameter and not an HTTP header -- none of item 69's
five existing no-parameter justifications (CORS's Origin header,
Host Header's Host header, CSRF's whole-form action, Auth's
state-machine test name, XXE's whole-body payload) describe a path
segment, but the underlying reasoning is the same shape: there is no
single query/body parameter in the conventional sense to name. Unlike
CORS's four signals sharing one endpoint (item 91's flagged `DEDUP_KEY`
collision risk), this is not a collision concern here: `endpoint`
(`target_url`, unchanged, matching every scanner's own convention of
recording the URL given to `scan()`) already differs across different
version-testable URLs, so `DEDUP_KEY`'s
`(vuln_type, endpoint_path, http_method, parameter)` stays distinct
per finding even with `parameter` always `None`.

`payload_used` HOLDS THE ALTERNATE URL, NOT A CONVENTIONAL PAYLOAD
STRING: `endpoint` records `target_url` (the URL `scan()` was given,
matching every other scanner's convention), so the alternate
(lower-version) URL that actually granted access needs to live
somewhere -- `payload_used` is the closest existing field to "what was
tried instead," the same role it plays in `ssrf_scanner.py`'s in-band
metadata checks (the metadata URL as data, not the scanner's own
connection target).

`interactsh_client` -- NOT DECLARED, same reasoning as every other
non-OOB scanner in this batch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "api_versioning_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention
DENIED_STATUS_CODE = 403


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from
    `api_versioning_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _find_lower_version_url(target_url: str, patterns: list[dict]) -> str | None:
    """Finds a version path segment in `target_url` (matching one of
    `patterns`' regexes) and returns the URL with the version number
    decremented by one.

    Args:
        target_url: The URL to search for a version segment.
        patterns: Payload entries, each with a `pattern` field -- a
            regex with one capturing group around the version digits.

    Returns:
        The alternate (one-version-lower) URL, or `None` if no pattern
        matches `target_url`, or the matched version is already `0`.
    """
    for entry in patterns:
        match = re.search(entry["pattern"], target_url)
        if match is None:
            continue
        version = int(match.group(1))
        if version <= 0:
            return None
        start, end = match.span()
        return f"{target_url[:start]}/v{version - 1}/{target_url[end:]}"
    return None


@register("api_versioning")
class APIVersioningScanner(BaseScanner):
    """Section 7.24. See module docstring for the URL-path injection
    shape and the two-part detection condition."""

    def __init__(self, session, *, payloads: list[dict] | None = None) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`. Each entry's
                `pattern` field is a regex identifying a version path
                segment -- see module docstring's "PATTERN LIVES IN THE
                PAYLOAD FILE" note.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            `[]` if no payload entries are loaded, no pattern matches
            `target_url`, the matched version is `0`, or `target_url`
            itself does not return 403. Otherwise a single
            `ExploitCandidate` if the alternate (lower) version does
            not return 403.
        """
        if not self._payloads:
            return []

        alternate_url = _find_lower_version_url(target_url, self._payloads)
        if alternate_url is None:
            return []

        current_response = await self.session.request("GET", target_url)
        if current_response.status_code != DENIED_STATUS_CODE:
            return []

        alternate_response = await self.session.request("GET", alternate_url)
        if alternate_response.status_code == DENIED_STATUS_CODE:
            return []

        return [self._candidate(target_url, alternate_url, alternate_response.text)]

    def _candidate(self, target_url: str, alternate_url: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="api_versioning",
            endpoint=target_url,
            http_method="GET",
            parameter=None,
            detected_by="api_versioning",
            payload_used=alternate_url,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
