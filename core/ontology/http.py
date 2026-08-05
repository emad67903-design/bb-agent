"""
Implements: Section 3 -- core/http/intercepting_client.py's traffic-log
entry shape (Section 10.5: "8 KB body cap; 100 K entry max; metadata
denylist").
Blueprint: bb_agent_v6.6_final_blueprint.md

AUTHORED JUDGMENT CALL, NOT BLUEPRINT-CITED (docs/DECISIONS.md, Week 5
section): the blueprint specifies InterceptingClient's caps and
suppression rule in detail (Section 10.5) but never gives a field list
for what one logged HTTP traffic entry actually contains. Placed in
`core/ontology/` per the ontology-first rule despite Section 3's
ontology/ tree not naming this file explicitly (only `enums.py`,
`surface.py`, `findings.py`, `state.py`, `helpers.py` are named there) --
the same reasoning `core/ontology/browser.py`'s `BrowserCapture`
placement already used: a shared, multi-consumer shape belongs in
ontology, not buried in the one file that happens to construct it
first. Kept deliberately minimal: every field is directly traceable to
Section 10.5's own vocabulary -- `method`/`url`/`status_code`/`headers`
are literally what gets logged when a body is suppressed ("log status
code + headers ONLY"); `body_preview`/`body_sha256`/`suppressed`
together encode the three-way choice Section 10.5 itself describes
(suppressed -> neither; <= 8 KB and not suppressed -> `body_preview`;
> 8 KB and not suppressed -> `body_sha256`, full body to
`data/telemetry/large_bodies/`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TrafficEntry:
    """One logged HTTP request/response pair (Section 10.5).

    Attributes:
        method: HTTP method (e.g. "GET", "POST").
        url: The request URL.
        status_code: The response's HTTP status code.
        headers: Response headers.
        body_preview: The response body text, if it was at or under
            `BODY_CAP_BYTES` (8,192) and the entry was not suppressed.
            `None` if suppressed, or if the body exceeded the cap (see
            `body_sha256` in that case).
        body_sha256: SHA-256 hex digest of the response body, set only
            when the body exceeded `BODY_CAP_BYTES` and the entry was
            NOT suppressed -- the full body is written to
            `data/telemetry/large_bodies/{body_sha256}` rather than kept
            inline (Section 10.5). `None` otherwise.
        suppressed: `True` if `_should_suppress_body` matched (direct
            metadata path, metadata URL in a query parameter, or
            metadata URL in the request body -- Section 10.5 + R-H3 +
            item 52's Content-Type-aware extension). Section 10.5: "When
            suppressed: log status code + headers ONLY. Never log
            response body." Both `body_preview` and `body_sha256` are
            `None` when this is `True`.
        timestamp: When this entry was recorded.
    """

    method: str
    url: str
    status_code: int
    headers: dict[str, str]
    body_preview: str | None
    body_sha256: str | None
    suppressed: bool
    timestamp: datetime
