"""
Implements: no direct Section 3 citation -- an authored addition, not a
blueprint-named file. Flagged as such here and in docs/DECISIONS.md item
74, not silently introduced.
Blueprint: bb_agent_v6.6_final_blueprint.md

WHY THIS FILE EXISTS, AUTHORED NOT CITED: `BaseScanner.scan(self,
target_url: str)` (item 73) gives every one of the 29 scanners exactly
one input -- a URL, nothing else. Every Batch 1 workflow (Section 7.1
XSS, 7.2 SQLi, 7.7 SSTI, 7.10 LFI, 7.18 Path Traversal) tests by
substituting a payload into a query parameter's value ("Reflection in
HTML body..." / "AND 1=1 vs AND 1=2" / "{{7*7}}" / "../../../etc/hostname"
-- every one of these is a value that replaces something already in the
URL, not a new attack surface the scanner invents). Five scanners doing
the identical "parse query params, substitute each one at a time with
each payload, build a candidate URL" mechanic is exactly the "same kind
of thing, many times" case the Engineering Constitution says to share,
not scatter across five near-identical copies.

SCOPE, DELIBERATELY NARROW -- GET QUERY PARAMETERS ONLY, NOT POST BODIES,
NOT HEADERS, NOT PATH SEGMENTS: `scan()`'s signature has no body, no
header dict, nothing but a URL string -- there is nothing else for this
batch's scanners to inject into with the input they're given. This is
the concrete instance the reviewer's own standing instruction (item 69)
anticipated: "the moment any of these scanners' detection signal needs
something [the current shape] can't express, stop and flag it back."
Flagging it here: testing POST-body parameters, headers, or path
segments for any of these five vuln types is real, common attack surface
this batch does NOT cover, because `scan(target_url: str)` has no way to
receive a body/header/path-segment to test. If `target_url` itself has
no query parameters, every function below correctly produces zero
candidates -- not an error, just nothing to test with this input shape.

`ExploitCandidate.parameter` IS SET FROM THIS MODULE'S OUTPUT: each
yielded `InjectionPoint.parameter` is the exact query-parameter name
whose value was substituted, matching Section 6.9's `DEDUP_KEY`
convention (docs/DECISIONS.md item 69) directly -- this module is where
that field's real value comes from for Batch 1's scanners.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionPoint:
    """One query-parameter substitution ready to test.

    Attributes:
        url: `target_url` with `parameter`'s value replaced by the
            payload that was substituted in.
        parameter: The query-parameter name that was substituted --
            feeds `ExploitCandidate.parameter` directly.
    """

    url: str
    parameter: str


def iter_query_param_injections(target_url: str, payload: str) -> list[InjectionPoint]:
    """Substitutes `payload` into each of `target_url`'s existing query
    parameters, one at a time, leaving every other parameter unchanged.

    Args:
        target_url: The endpoint under test. If it has no query string
            at all, this returns an empty list -- there is nothing to
            substitute into (see module docstring: this batch's
            scanners have no other input to fall back on).
        payload: The literal string to substitute as each parameter's
            new value, already fully rendered (any `{marker}`-style
            placeholder already resolved by the caller -- this function
            does no templating of its own).

    Returns:
        One `InjectionPoint` per existing query parameter, in the order
        `urllib.parse.parse_qs` returns them. Empty list if `target_url`
        has no query string, or if parsing finds no parameters.

    Example:
        >>> [p.parameter for p in iter_query_param_injections("https://x.com/search?q=a&page=2", "PAYLOAD")]
        ['q', 'page']
        >>> iter_query_param_injections("https://x.com/search?q=a", "PAYLOAD")[0].url
        'https://x.com/search?q=PAYLOAD'
    """
    parsed = urllib.parse.urlsplit(target_url)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if not params:
        return []

    points: list[InjectionPoint] = []
    for name in params:
        new_params = {k: (v[0] if k != name else payload) for k, v in params.items()}
        new_query = urllib.parse.urlencode(new_params, quote_via=urllib.parse.quote)
        new_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
        )
        points.append(InjectionPoint(url=new_url, parameter=name))
    return points
