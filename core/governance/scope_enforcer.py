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

Built here (this file), Week 6 (docs/DECISIONS.md item 65): the
`credential_validation_allowlist` exemption branch inside `is_allowed`
-- Section 4.4's `if (caller_id == "hardcoded_credentials" and
credential_validation_allowlist.enabled and host in
credential_validation_allowlist.external_apis)` branch, verbatim -- per
Section 12's Week 6 row ("... `credential_validation_allowlist` in
`scope_enforcer.py`"). Landed as a new keyword-only
`credential_validation_allowlist: CredentialValidationAllowlist | None
= None` parameter -- the same treatment as `scope_domains` (Week 3): an
explicit parameter, never hidden module state or call-stack inspection,
per the Engineering Constitution's "EXPLICIT PARAMETERS, NEVER RUNTIME
INTROSPECTION" rule, applied consistently to both rather than inventing
a second convention for the same kind of thing. Defaults to `None` so
no Week 3/5 caller (`browser_tool.py`, `network_observer.py`,
`rate_limited_client.py`) needs to change -- confirmed: none of them
pass a non-`None` `caller_id` either, so the exemption branch cannot
fire through any of them regardless of this change. The branch guards
explicitly against a `None` allowlist (`credential_validation_allowlist
is not None and ...`) rather than assuming, as Section 4.4's own
pseudocode does, that the object is always available -- the blueprint's
snippet lives in a world where both `scope_domains` and
`credential_validation_allowlist` are pre-existing module/closure
state; this codebase made `scope_domains` explicit at Week 3, so
`credential_validation_allowlist` follows that same, now-established
pattern.

Built here (this file), Week 7 (docs/DECISIONS.md item 71 -- supersedes
this note's own prior text, which is preserved below the line for the
record rather than deleted): `is_allowed_outbound` / `METADATA_HOSTS` /
`METADATA_IP` / `INTERACTSH_SUFFIX`, Section 4.4 verbatim. Moved here
from `core/sandbox/safety_guard.py` (its first real implementation,
Week 6, docs/DECISIONS.md item 66), which now imports all four names
from this module instead of keeping its own copy. Prompted by Week 7's
CMDi/XXE/Deserialization/SSRF scanners needing the identical
interactsh+metadata+scope policy through the main
`RateLimitedClient`/`InterceptingClient` path, not the sandbox's
locally-scoped one. The layering concern item 66 raised implicitly by
choosing to stay local ("this file makes its own decision... nothing
about the sandbox depends on `browser_tool.py`, `RateLimitedClient`, or
the Go services being correct") is about the ENFORCEMENT CALL SITE
staying independent per Section 10.2's four-layer framing -- it was
never a claim that `core/sandbox` importing FROM `core/governance` is
itself architecturally barred, and no such barrier exists: this file's
own `_is_scope_allowed` was already imported by `safety_guard.py`
(line 106) before this change, so a second, larger import from the same
module changes nothing about that direction. Each of `is_allowed`,
`is_allowed_outbound`, and Go's `scope_guard.go` remains an independent
CALL, exactly as before -- only the Python-side FUNCTION DEFINITION
itself is now singular rather than duplicated between two files that
had started drifting apart in signature already (see `is_allowed_outbound`'s
own docstring below for the `str | None`/`list[str]` vs. this note's
original `str`/`set[str]` mismatch that made the duplication concrete,
not just theoretical, before this consolidation).

ORIGINAL (Week 3-6) TEXT, preserved for the record -- no longer
accurate as of item 71, kept so this docstring's own history is
traceable rather than silently rewritten: "Still deliberately NOT built
here (still gaps, not silently resolved): `is_allowed_outbound` /
`METADATA_HOSTS` / the `.interactsh.com` and 169.254.169.254 exceptions
(Section 4.4). These back the Python HTTP layer
(`intercepting_client.py`), not Playwright... `intercepting_client.py`'s
own Week 5 implementation does not import or reference this module at
all... `browser_tool.py`'s own scope-check skeleton calls only
`is_allowed(url)` -- never `is_allowed_outbound` -- so nothing built so
far needs it." That premise (nothing built needs it) is what changed:
Week 7's OOB-confirmed scanner batch is the first real caller on the
`RateLimitedClient` side.

Wiring `credential_validation_allowlist` into `RateLimitedClient`
    (Week 5) or `call_target()` (sandbox, this week). `is_allowed()`
    itself now supports the exemption end-to-end, but no caller
    constructs a `RateLimitedClient` with
    `caller_id="hardcoded_credentials"` yet -- `hardcoded_credentials.py`
    (the only component Section 4.4 permits to use this exemption) is
    Week 7 scope (Section 3's scanner-file listing), not built yet
    (repo-wide grep confirms zero references). Same "framework now,
    wiring later" pattern already used for `token_throttler.py`
    (docs/DECISIONS.md item 9). Go `scope_guard.go` (Week 0, already
    independent per Section 10.2 item 4) never carries this exemption at
    all -- Section 4.4 scopes `credential_validation_allowlist` to
    `hardcoded_credentials.py`'s Python-side HTTP calls only, not the Go
    race/smuggling services.

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
from core.ontology.scope import CredentialValidationAllowlist

# Section 4.4's is_allowed_outbound exceptions (docs/DECISIONS.md item 71;
# moved from core/sandbox/safety_guard.py, which now imports these three
# names from here instead of defining its own copies).
INTERACTSH_SUFFIX = ".interactsh.com"
METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
METADATA_IP = "169.254.169.254"


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
    credential_validation_allowlist: CredentialValidationAllowlist | None = None,
) -> bool:
    """Hard-gate scope check: is `url`'s host in scope?

    Section 4.4 (v6.5/V6.4-M2 fix): callers identify themselves via an
    explicit `caller_id` string, set once at the caller's own
    construction time and carried on its instance -- never via call-stack
    inspection, which the blueprint documents as costing 50-200ms per
    call at Fast Lane volume. `browser_tool.py`, `network_observer.py`,
    and `rate_limited_client.py` are this codebase's callers so far; per
    Section 10.2 items 1-2, each must call this before making its
    respective request.

    Args:
        url: The full URL a caller wants to reach.
        scope_domains: The program's in-scope domain patterns (see
            `_is_scope_allowed`). Callers load this once (e.g. at their
            own construction) rather than re-reading `scope.yaml` per
            call -- re-parsing YAML on every scope check would reproduce
            the exact per-call-cost problem `caller_id` itself was
            introduced to avoid (Section 4.4).
        caller_id: Identifies the calling component. Only meaningful
            value today: `"hardcoded_credentials"`, which combined with
            `credential_validation_allowlist` unlocks the Week 6
            exemption below. Any other value (including `None`) behaves
            identically to `None` -- it simply cannot match the
            exemption's `caller_id ==` check.
        credential_validation_allowlist: The Week 6 (docs/DECISIONS.md
            item 65) `credential_validation_allowlist` exemption data,
            normally loaded once via
            `core.governance.scope_config_generator.load_credential_validation_allowlist`
            and carried by the caller, mirroring how `scope_domains`
            itself is loaded once and carried. `None` (the default)
            means "this caller never offers the exemption" -- not an
            error; every caller except a future `hardcoded_credentials.py`
            is expected to leave this `None` permanently.

    Returns:
        `True` if `url`'s host is in scope, OR if the
        `credential_validation_allowlist` exemption applies (`caller_id
        == "hardcoded_credentials"` AND the allowlist is provided,
        enabled, and lists `url`'s host) -- else `False`. Never raises
        on a malformed `url`; an unparseable host is simply out of
        scope (fails closed), and a malformed/missing allowlist simply
        fails the exemption rather than raising (also fails closed).
    """
    host = urllib.parse.urlparse(url).hostname
    if _is_scope_allowed(host, scope_domains):
        return True
    if (
        caller_id == "hardcoded_credentials"
        and credential_validation_allowlist is not None
        and credential_validation_allowlist.enabled
        and host in credential_validation_allowlist.external_apis
    ):
        return True
    return False


def is_allowed_outbound(dst_host: str | None, dst_ip: str | None, scope_domains: list[str]) -> bool:
    """Section 4.4's `is_allowed_outbound`, verbatim -- the three-way
    "interactsh + metadata + scope" policy (Section 3's `safety_guard.py`
    comment), now canonical here rather than duplicated (docs/DECISIONS.md
    item 71). Ported unchanged from `core/sandbox/safety_guard.py`'s
    Week 6 implementation (item 66), including that version's `dst_host
    is None` fail-closed handling -- not literally Section 4.4's bare
    code block (which has no None-handling at all, consistent with every
    other Section 4.4 pseudocode snippet in this codebase, e.g.
    `_is_scope_allowed` above already adds the same handling for the
    same reason).

    Intended callers: anything on the *Python HTTP layer* side needing
    the interactsh/metadata exceptions `is_allowed()` alone does not
    grant -- Week 7's CMDi/XXE/Deserialization/SSRF scanners (OOB
    confirmation, IMDSv2 metadata probing) via `RateLimitedClient`, and
    `core/sandbox/safety_guard.py`'s `call_target()` (unchanged
    behavior, now via import). `is_allowed()` itself is NOT changed by
    this addition -- Playwright/`browser_tool.py`'s scope check still
    calls only `is_allowed()`, deliberately: Section 4.4 titles the
    interactsh/metadata exceptions as backing the Python HTTP layer
    specifically, not Playwright, and nothing in this change extends
    that policy to a call site Section 4.4 never named.

    Args:
        dst_host: The destination URL's hostname, or `None` if it could
            not be parsed (fails closed -- see Returns).
        dst_ip: The destination hostname's resolved IP, or `None` if
            resolution failed or was not attempted. Real DNS resolution
            (e.g. `safety_guard._resolve_ip`) is the caller's
            responsibility -- this function only compares the value it's
            given, matching Section 4.4's own signature, which takes
            `dst_ip` as a parameter rather than resolving it internally.
        scope_domains: The program's in-scope domain patterns.

    Returns:
        `True` if `dst_host` is an interactsh subdomain, a known cloud
        metadata hostname, resolves to the metadata IP, or is in scope
        (via `_is_scope_allowed`'s wildcard matching). `False` (fail
        closed) if `dst_host` is `None`.
    """
    if dst_host is None:
        return False
    return (
        dst_host.endswith(INTERACTSH_SUFFIX)
        or dst_host in METADATA_HOSTS
        or dst_ip == METADATA_IP
        or _is_scope_allowed(dst_host, scope_domains)
    )


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
