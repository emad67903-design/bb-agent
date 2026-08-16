"""
Implements: no direct Section 3 citation -- an authored addition, not a
blueprint-named file. Flagged here and in docs/DECISIONS.md item 78, not
silently introduced -- same disclosure pattern as `param_injection.py`
(item 74).
Blueprint: bb_agent_v6.6_final_blueprint.md

WHY THIS FILE EXISTS: `lfi_scanner.py` (Section 7.10) and
`path_traversal.py` (Section 7.18) both test the identical Linux target
file, `/etc/hostname`, for the identical reason -- Section 7.18
explicitly: "Also handles Windows LFI deferred from lfi_scanner.py," and
both sections' "Safe exploit" text is the same non-sensitive read
(`/etc/hostname`... "Non-sensitive; no PII"). Sharing the one heuristic
that decides "does this response look like it actually contains
`/etc/hostname`'s content" keeps both scanners' idea of what counts as a
hit identical by construction, rather than two hand-written regexes that
could silently drift apart. `path_traversal.py` alone also tests a
second target, Windows' `win.ini` (Section 7.18) -- kept in this same
module as the other content-shape heuristic, even though only one
scanner uses it this batch, since it is the same KIND of thing
(`param_injection.py`'s docstring makes the analogous "same kind of
thing, many times, share it" argument for query-parameter substitution;
this is the "content-shape check for a path-traversal target" version
of that).

THE HOSTNAME HEURISTIC IS AUTHORED, NOT BLUEPRINT-SPECIFIED; THE WIN.INI
ONE IS NOT A HEURISTIC AT ALL -- SEE EACH FUNCTION'S OWN DOCSTRING FOR
WHY: Section 7.10 says only "`/etc/hostname` in response body" -- it
does not say how to recognize hostname CONTENT without already knowing
the target's real hostname value (which this codebase has no way to know
in advance), so `looks_like_hostname_content` is a three-part
approximation, its limits documented on the function itself.
`looks_like_win_ini_content` has no equivalent problem: `win.ini`'s
first two section headers are fixed, well-known constants independent
of the target machine, so that check is a real, reliable exact-substring
match, not an approximation.
"""

from __future__ import annotations

import re

MAX_HOSTNAME_LENGTH = 253  # RFC 1035
HOSTNAME_SHAPE_PATTERN = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def looks_like_hostname_content(candidate_text: str, baseline_text: str) -> bool:
    """Three checks, all required -- see module docstring for why this
    is an approximation, not a proof:
      1. Differs from the unmodified baseline response (the core
         `differential` signal both scanners' Evidence lines already
         list).
      2. Short: stripped content <= 253 characters, the DNS maximum
         total hostname length (RFC 1035) -- rules out a full HTML
         error page being mistaken for a one-line file.
      3. Hostname-shaped: matches RFC 1035's character class (letters,
         digits, hyphens, dots as label separators, <= 63 chars per
         label) via `HOSTNAME_SHAPE_PATTERN`.
    No check here can prove the content IS `/etc/hostname` rather than
    some other short, differing, alphanumeric response -- a real,
    inherent limit of black-box testing without a known-good baseline
    value, not hidden behind confident-sounding code.

    Args:
        candidate_text: The response body from a path-traversal probe
            targeting `/etc/hostname`.
        baseline_text: The unmodified endpoint's response body, for the
            required differential check.

    Returns:
        `True` if all three checks pass.
    """
    stripped = candidate_text.strip()
    if not stripped or stripped == baseline_text.strip():
        return False
    if len(stripped) > MAX_HOSTNAME_LENGTH:
        return False
    return bool(HOSTNAME_SHAPE_PATTERN.match(stripped))


def looks_like_win_ini_content(candidate_text: str, baseline_text: str) -> bool:
    """`path_traversal.py`'s Windows-target counterpart to
    `looks_like_hostname_content` (Section 7.18: "Windows:
    ..\\..\\..\\..\\windows\\win.ini"). Far more reliable than the
    hostname heuristic above: `win.ini` is a fixed, well-known Windows
    file whose first two section headers, `[fonts]` and `[extensions]`,
    are present on every stock Windows install regardless of hostname,
    locale, or installed fonts -- an exact-substring check, not a shape
    approximation, and correspondingly not flagged as a fuzzy heuristic
    the way the hostname check is.

    Args:
        candidate_text: The response body from a path-traversal probe
            targeting `win.ini`.
        baseline_text: The unmodified endpoint's response body, for the
            required differential check.

    Returns:
        `True` if both `[fonts]` and `[extensions]` (case-insensitive)
        are present and the content differs from baseline.
    """
    stripped = candidate_text.strip()
    if not stripped or stripped == baseline_text.strip():
        return False
    lowered = stripped.lower()
    return "[fonts]" in lowered and "[extensions]" in lowered
