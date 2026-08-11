"""
Implements: local, minimal prompt-injection mitigation for seven
files ONLY -- the six core/mental_model/ Groq/local-7B-calling files:
role_mapper.py (via flow_tracer.py's shared call), flow_tracer.py,
boundary_identifier.py, assumption_extractor.py,
exploitability_scorer.py, builder.py; plus, as a named, explicit,
ratified exception (docs/DECISIONS.md item 64's Section 39
architectural review), core/planning/hypothesis_engine.py.
Blueprint: bb_agent_v6.6_final_blueprint.md

SEVENTH CALLER, RATIFIED: hypothesis_engine.py's `seed_hypothesis` uses
only `detect_injection_markers` (not `truncate_and_delimit`, which has
no call site there -- see that module's own docstring) to flag
suspicious phrasing in candidate `vuln_type`/`endpoint` strings, which
may ultimately derive from MentalModel/target-controlled content.
Detection-only, logged not blocked, same as every other caller below --
this exception does not change this module's behavior, only its
documented caller list.

NOT `core/governance/content_sanitizer.py` -- deliberately (see
docs/DECISIONS.md item 41 for the full record). Section 3 names
`content_sanitizer.py` with a five-word comment ("Prompt injection
defence") and gives it no field list, no method signature, no week
assignment anywhere in Section 12 (grep-confirmed). Designing that
general, project-wide component now -- from a single call site's
concrete needs -- would mean guessing its real shape from one example,
the same failure mode `TargetAdapter`'s original per-`TargetType`
weight table would have been (items 34/35). This module is scoped
narrowly on purpose: it exists only because these files feed
attacker-reachable content (raw page HTML in `flow_tracer.py`'s case;
page-derived evidence strings everywhere downstream, including
hypothesis candidates derived from them) into LLM prompts or, for
hypothesis_engine.py specifically, into content that may later reach
one. `content_sanitizer.py` itself remains unspecified and unassigned,
to be designed once enough real callers exist to generalize a real
interface from, rather than invented in the abstract now.

PRIVATE HELPER (leading underscore), same convention as
`_groq_client.py`: introduces no new public path Section 3 names,
imported only by the seven files above.

DETECTION, NOT SANITIZATION: `detect_injection_markers` flags
suspicious phrasing: it does not strip, rewrite, or block anything.
Callers (currently `flow_tracer.py`) decide what to do with a positive
detection -- in this codebase, that means recording it as a signal
(`PageSignals.injection_marker_detected`) and logging
`[MENTAL_MODEL_INJECTION_SUSPECTED]`, not silently dropping the
content. A heuristic regex match is not proof of an actual attack (it
can false-positive on legitimate pages that happen to discuss prompt
injection, for instance); treating a match as evidence to surface,
rather than content to censor, avoids that heuristic's false-positive
rate silently deleting real recon signal.
"""

from __future__ import annotations

import re

_INJECTION_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+|the\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"\bsystem\s*:", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"\boverride\s+(the\s+)?instructions?\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(all\s+|the\s+)?(above|previous|prior)\b", re.IGNORECASE),
)


def detect_injection_markers(text: str) -> bool:
    """Heuristic check for instruction-like phrasing that could be an
    attempt to redirect an LLM reading this text as page content.

    Detection only -- does not modify `text` in any way. A `True`
    result means "this looks suspicious, treat it as a signal to
    report," not "this is confirmed malicious" or "this must be
    removed."

    Args:
        text: Any string a caller is about to embed in an LLM prompt as
            page-derived content (raw HTML, or an evidence string
            extracted from it).

    Returns:
        `True` if any known injection-marker pattern matches anywhere
        in `text`, else `False`. Empty or `None`-like input (falsy
        `text`) always returns `False` -- nothing to match.
    """
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_MARKER_PATTERNS)


def truncate_and_delimit(value: str, max_len: int = 300) -> str:
    """Truncates `value` to `max_len` characters and wraps it in an
    explicit data delimiter, for any string crossing from one call's
    output into the next call's prompt (e.g. a `PageSignals` evidence
    string `flow_tracer.py` produced, about to be embedded in
    `boundary_identifier.py`'s Groq-call-1 prompt).

    Truncating bounds how much of any single value can occupy prompt
    space (a very long "evidence" string would otherwise let page
    content dominate the prompt); delimiting marks the boundary
    explicitly so the surrounding prompt structure stays visually
    distinguishable from the embedded value, for a human reviewing logs
    as much as for the model itself.

    Args:
        value: The string to bound and delimit.
        max_len: Maximum characters kept from `value` before the
            truncation marker. Defaults to 300 -- short enough that a
            single evidence string can't dominate a prompt built from
            many pages' signals, long enough to keep a real evidence
            snippet (a form action, a short HTML fragment) legible.

    Returns:
        `"<<<DATA>>>{truncated value}<<<END DATA>>>"`, with `"...[truncated]"`
        appended before the closing delimiter if `value` exceeded
        `max_len`.
    """
    truncated = value[:max_len]
    suffix = "...[truncated]" if len(value) > max_len else ""
    return f"<<<DATA>>>{truncated}{suffix}<<<END DATA>>>"
