"""
Implements: Section 3 -- core/governance/scope_enforcer.py ("HARD GATE;
wildcard-aware"). Also implements Section 4.4's `_is_scope_allowed` and
`is_allowed` code blocks (the v6.5/V6.4-M2 explicit-`caller_id` version),
and is called per Section 10.2's four-layer scope-enforcement list (item
2: "browser_tool.py (all Playwright, R-H1 fix)").
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE-GAP NOTE (documented judgment call; see docs/DECISIONS.md item 31
for the full record): Section 12 never assigns this file to any week.
It's referenced constantly (Sections 2, 3, 4.4, 7.28, 10.1, 10.2, 14, 16)
and Week 6's row adds `credential_validation_allowlist` TO it ("...in
`scope_enforcer.py`"), which presupposes the file already exists by
Week 6 -- but no row anywhere says "build scope_enforcer.py." This is
the same *kind* of gap as `approval_manager.py`/`telegram_bot.py`
(core/governance/safety_gate.py's own docstring, and docs/DECISIONS.md's
Week 2 section, on `IntentEngine`) -- a component referenced everywhere,
owned nowhere in Section 12.

Handled differently from that precedent, deliberately, and the
difference is the reason a stub isn't used here: `route_tier_d_action`
(safety_gate.py) stubs out `approval_manager.py` behind a fail-closed
Protocol because the real thing is an asynchronous, human-in-the-loop
round trip that genuinely cannot be faked or partially built. Scope
checking has no such property -- Section 4.4 gives the complete,
deterministic logic verbatim (wildcard host matching against a fixed
domain list), and this week's own Resolution 1 (docs/DECISIONS.md item
29) already established the operating principle for exactly this
situation: a Week 3 deliverable that cannot functionally complete
without a prerequisite existing is grounds to build that prerequisite
now, not defer it. `browser_tool.py`'s scope check calling a stub that
always fails closed would make `browser_tool.py` permanently
non-functional, which is a materially different (and worse) outcome
than the approval-workflow stub, which fails closed on a genuinely rare
and human-mediated path.

Built here (this file), Week 3: `_is_scope_allowed` and `is_allowed`,
exactly as Section 4.4 specifies them, including the explicit `caller_id`
parameter (v6.5/V6.4-M2 fix) rather than call-stack inspection.

Deliberately NOT built here (still gaps, not silently resolved):
  - `is_allowed_outbound` / `METADATA_HOSTS` / the `.interactsh.com` and
    169.254.169.254 exceptions (Section 4.4). These back the *Python
    HTTP layer* (`intercepting_client.py`), not Playwright -- Section
    4.4 titles that code block "Python HTTP layer
    (scope_enforcer.py + intercepting_client.py)" specifically, and
    `intercepting_client.py` has an explicit Week 5 assignment (Section
    12). `browser_tool.py`'s own scope-check skeleton (Section 3) calls
    only `is_allowed(url)` -- never `is_allowed_outbound` -- so nothing
    Week-3-scoped needs it.
  - The `credential_validation_allowlist` exemption branch inside
    `is_allowed` (Section 4.4's `if (caller_id == "hardcoded_credentials"
    ...)` branch). Explicitly Week 6 (Section 12's Week 6 row: "...
    `credential_validation_allowlist` in `scope_enforcer.py`"). The
    `caller_id` parameter is accepted now (so Week 6 can add that branch
    without changing this function's signature or any Week-3 caller),
    but it does nothing yet -- there is no allowlist to check against
    until Week 6 builds it.
  - Wiring into `RateLimitedClient` (Week 5), Go `scope_guard.go` (Week
    0, already independent per Section 10.2 item 4), or `call_target()`
    sandbox (Week 6). This file exists to be called; Week 3 only adds
    one caller (`browser_tool.py`).

Reuses `core.governance.scope_config_generator.load_scope_domains` for
reading `configs/scope.yaml`'s `scope_domains` list rather than adding a
second YAML parser -- that module's own docstring states it is "the
single place that knows how to read configs/scope.yaml"; duplicating
that logic here would recreate the exact multi-parser drift risk this
project's Engineering Constitution singles out ontology types for (the
same discipline extends naturally to config-reading).
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from core.governance.scope_config_generator import load_scope_domains


def _is_scope_allowed(dst_host: str | None, scope_domains: list[str]) -> bool:
    """Wildcard-aware host match against an in-scope domain list.

    Transcribed verbatim from Section 4.4: `*.example.com` matches both
    `example.com` and any subdomain of it; a bare pattern matches only
    that exact host.

    Args:
        dst_host: The hostname to check (e.g. from
            `urllib.parse.urlparse(url).hostname`). May be `None` if the
            URL had no parseable hostname, in which case this always
            returns `False` -- an unparseable host is never in scope.
        scope_domains: The program's in-scope domain patterns, as
            returned by
            `core.governance.scope_config_generator.load_scope_domains`.

    Returns:
        `True` if `dst_host` matches an entry in `scope_domains`
        (exactly, or via a `*.` wildcard prefix), else `False`.
    """
    if dst_host is None:
        return False
    for pattern in scope_domains:
        if pattern.startswith("*."):
            base = pattern[2:]
            suffix = "." + base
            if dst_host == base or dst_host.endswith(suffix):
                return True
        elif dst_host == pattern:
            return True
    return False


def is_allowed(
    url: str,
    scope_domains: list[str],
    *,
    caller_id: str | None = None,
) -> bool:
    """Hard-gate scope check: is `url`'s host in scope?

    Section 4.4 (v6.5/V6.4-M2 fix): callers identify themselves via an
    explicit `caller_id` string, set once at the caller's own
    construction time and carried on its instance -- never via call-stack
    inspection, which the blueprint documents as costing 50-200ms per
    call at Fast Lane volume. `browser_tool.py` is this week's only
    caller; per Section 10.2 item 2, it must call this before every
    `page.goto()`.

    Args:
        url: The full URL a caller wants to reach.
        scope_domains: The program's in-scope domain patterns (see
            `_is_scope_allowed`). Callers load this once (e.g. at their
            own construction) rather than re-reading `scope.yaml` per
            call -- re-parsing YAML on every scope check would reproduce
            the exact per-call-cost problem `caller_id` itself was
            introduced to avoid (Section 4.4).
        caller_id: Identifies the calling component. Reserved for Week
            6's `credential_validation_allowlist` exemption
            (`caller_id == "hardcoded_credentials"`); accepted now for
            signature stability but not yet acted on -- see this
            module's docstring.

    Returns:
        `True` if `url`'s host is in scope, else `False`. Never raises
        on a malformed `url`; an unparseable host is simply out of
        scope (fails closed).
    """
    del caller_id  # Week 6 will consume this; unused until that allowlist exists.
    host = urllib.parse.urlparse(url).hostname
    return _is_scope_allowed(host, scope_domains)


def load_scope_domains_for_enforcement(scope_yaml_path: Path) -> list[str]:
    """Thin, explicitly-named wrapper over
    `scope_config_generator.load_scope_domains`, so callers in
    `core/governance/` and `core/browser/` import scope-domain loading
    from this module rather than reaching into
    `scope_config_generator.py` (whose own docstring scopes it to
    `scope_allowed.json` generation and `program_type` reading, not
    general-purpose scope-domain access for runtime enforcement).

    Args:
        scope_yaml_path: Path to `configs/scope.yaml`.

    Returns:
        The `scope_domains` list, in file order, duplicates removed.

    Raises:
        core.governance.scope_config_generator.ScopeConfigError: If the
            file is missing, is not valid YAML, has no `scope_domains`
            key, or `scope_domains` is empty or contains non-string
            entries -- identical failure modes to the underlying
            function, since this is a direct delegation, not a
            reimplementation.
    """
    return load_scope_domains(scope_yaml_path)
