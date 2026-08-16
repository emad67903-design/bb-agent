"""
Implements: Section 3 -- core/sandbox/safety_guard.py ("Outbound block
except interactsh + metadata + scope"), and Section 10.7's pre-approved
`call_target()`:
    def call_target(url: str, method: str = "GET",
                    headers: dict = None, body: str = None) -> dict:
        '''Scope-checked, rate-limited. Returns {"status": int,
        "headers": dict, "body_preview": str (512 chars)}'''
Blueprint: bb_agent_v6.6_final_blueprint.md

WHERE "interactsh + metadata + scope" COMES FROM, AND WHERE IT LIVES NOW
(docs/DECISIONS.md items 66 and 71): Section 3's one-line comment for
this file is not a free-standing spec -- it describes the same
three-way policy Section 4.4 writes out in full, in a function of that
name. Week 6 (item 66) built that function's first real implementation
here, scoped local, because nothing else in the codebase needed it yet.
Week 7 (item 71) moved the function itself -- and its three constants,
`INTERACTSH_SUFFIX` / `METADATA_HOSTS` / `METADATA_IP` -- to
`core/governance/scope_enforcer.py`, imported below, once
CMDi/XXE/Deserialization/SSRF scanners needed the identical policy on
the `RateLimitedClient` side. This file's own `is_allowed_outbound` /
`check_outbound` / `_augmented_scope_domains` behavior is UNCHANGED by
that move -- same inputs, same outputs, same fail-closed-on-`None`
semantics -- confirmed by running this file's existing 25 tests
unmodified before and after the refactor (docs/DECISIONS.md item 71).
Only the function's DEFINITION moved; the CALL SITE (this file's
`check_outbound`, still the one and only thing `call_target()` asks)
did not, preserving Section 10.2's "four independent layers" framing --
this file still makes its own decision, at its own point in the code,
independent of `browser_tool.py`/`RateLimitedClient`/the Go services.

`_is_scope_allowed` IS IMPORTED FROM `scope_enforcer.py`, NOT
REIMPLEMENTED, same as before item 71 and for the same reason: the
wildcard-matching algorithm is the SAME algorithm Week 3 already wrote,
tested, and Section 4.4 defines once. `is_allowed_outbound` now follows
the identical precedent -- imported, not reimplemented -- rather than
this file continuing to hold a second, independently-maintained copy
that had already started drifting from `scope_enforcer.is_allowed`'s
own signature conventions (`list[str]` for `scope_domains` throughout
this module vs. Section 4.4's literal `set[str]`; this file's version
already used `list[str]`, matching this module's `_is_scope_allowed`
import rather than the blueprint's bare snippet -- one more concrete
sign the two copies were two maintenance burdens, not one, even before
any behavior actually diverged).

`dst_ip`-BASED CHECKING IS A REAL DNS RESOLUTION, NOT A STRING
SHORTCUT: an earlier design considered simply appending
`"*.interactsh.com"` / `"169.254.169.254"` / `"metadata.google.internal"`
to `scope_domains` and letting `_is_scope_allowed`'s existing wildcard
logic do all the work -- workable for the hostname-string cases, but it
silently drops Section 4.4's `dst_ip == '169.254.169.254'` branch
entirely (a hostname that is NOT itself `169.254.169.254` or
`metadata.google.internal`, but that DNS resolves to the metadata IP --
a DNS-rebinding-style SSRF against the metadata endpoint). Section 4.4's
own function signature already takes `dst_ip` as a real parameter, so
implementing that branch with a real `socket.gethostbyname()` lookup is
transcription of what's already specified, not new scope. Resolution
failure (unresolvable host, or the sandboxed environment simply having
no DNS) fails to `None` for `dst_ip`, which fails that ONE branch open
-- `is_allowed_outbound` still evaluates its other three branches
normally, exactly as it would for any target whose IP nobody asked for.

CALL_TARGET RUNS INSIDE THE CHILD PROCESS, NOT THE PARENT: it is
injected into the untrusted code's `exec()` globals by
`sandbox_validator.build_restricted_globals`, which runs in the same
child `code_executor.py` forks for the untrusted code itself (see that
module's docstring). `call_target`'s own body is therefore ordinary,
non-restricted Python -- it may (and does) `import httpx`/`socket`
itself; the AST blocklist governs only the untrusted string being
executed, never the trusted harness code that executes it.

`call_target` USES `RateLimitedClient`, NOT A RAW HTTP CALL: the
Engineering Constitution's "ONE HTTP LAYER, NO EXCEPTIONS" applies to
this code too, trusted or not. A single `RateLimiter` is constructed
once per sandboxed execution (by `build_call_target`, at closure-
construction time) and shared across every `call_target()` invocation
the script makes -- a FRESH `RateLimitedClient`/`InterceptingClient` is
built per individual call (each wrapped in its own `asyncio.run()`,
since `call_target`'s blueprint-specified signature is synchronous, not
`async def`) but the shared `RateLimiter` instance is passed into every
one of them, so per-host timing state persists across calls within one
script's run instead of resetting each time. Verified empirically before
being written this way: constructing a fresh client per call but
sharing one `RateLimiter` correctly serializes timing across separate
`asyncio.run()` calls (each of which gets its own event loop) -- reusing
a single `httpx.AsyncClient` itself across separate `asyncio.run()`
calls is the actual hazard (a transport bound to a now-closed event
loop), which this design avoids by never doing that.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.parse

import httpx

from core.governance.scope_enforcer import (
    INTERACTSH_SUFFIX,
    METADATA_HOSTS,
    METADATA_IP,
    is_allowed_outbound,
)
from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient, RateLimiter

CALL_TARGET_BODY_PREVIEW_CHARS = 512


class SandboxOutOfScopeError(Exception):
    """Raised by a sandboxed `call_target()` closure when a script asks
    it to reach a host that is neither in scope, an interactsh domain,
    nor a cloud metadata endpoint. Named and messaged like
    `browser_tool.OutOfScopeError` / `rate_limited_client.OutOfScopeError`
    (`"<ComponentName> blocked: {url}"`) -- Section 10.2's four scope-
    enforcement layers each raise their own type on their own call site
    (`rate_limited_client.py`'s own docstring explains why this pattern
    is deliberate, not duplication)."""


def _resolve_ip(host: str) -> str | None:
    """Best-effort forward DNS lookup. Returns `None` on any resolution
    failure (unknown host, no DNS available, etc.) rather than raising
    -- an unresolvable host simply cannot match the `dst_ip ==
    METADATA_IP` branch of `is_allowed_outbound`; it does not block the
    other three branches from being evaluated normally."""
    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, OSError):
        return None


def check_outbound(url: str, scope_domains: list[str]) -> bool:
    """Parses `url` and resolves its host, then applies
    `is_allowed_outbound`. The single entry point `call_target()` uses
    -- callers needing the lower-level pieces (e.g. tests) can use
    `is_allowed_outbound` directly with a synthetic host/ip instead of
    triggering a real DNS lookup.

    Args:
        url: The full URL a sandboxed script wants to reach.
        scope_domains: The program's in-scope domain patterns.

    Returns:
        Whether `url` is allowed outbound from the sandbox.
    """
    host = urllib.parse.urlparse(url).hostname
    ip = _resolve_ip(host) if host is not None else None
    return is_allowed_outbound(host, ip, scope_domains)


def _augmented_scope_domains(scope_domains: list[str]) -> list[str]:
    """`scope_domains` plus literal patterns for the interactsh/metadata
    exceptions, expressed as ordinary `_is_scope_allowed` patterns.

    Why this exists: `check_outbound` (via `is_allowed_outbound`) is the
    real, authoritative decision for whether a sandboxed script's
    `call_target()` call may proceed -- but the actual HTTP call is then
    made through `RateLimitedClient`, which independently re-runs its
    own `scope_enforcer.is_allowed()` check (Section 10.2: every layer
    enforces scope on its own call site, `RateLimitedClient` does not
    know this call already cleared a *different* layer's broader
    policy). Without this, an already-approved interactsh or metadata
    call would be approved by `check_outbound` and then immediately
    re-rejected by `RateLimitedClient.request()` -- caught exactly this
    way, empirically, before this function existed: a smoke test calling
    `169.254.169.254` raised `RateLimitedClient`'s own `OutOfScopeError`
    despite `check_outbound` returning `True` for the same URL.

    `_is_scope_allowed`'s wildcard rule already treats `*.interactsh.com`
    as "exactly interactsh.com OR any subdomain" -- very slightly
    broader than `is_allowed_outbound`'s literal `dst_host.endswith(
    '.interactsh.com')` (which excludes the bare domain). That
    difference is intentional and harmless here: `is_allowed_outbound`
    already made the real decision and returned `True` before this
    function is ever called; this list only needs to make
    `RateLimitedClient`'s redundant re-check agree, not repeat the
    original decision's exact boundary. The metadata IP and hostname are
    added as exact (non-wildcard) entries, matching `METADATA_HOSTS`
    precisely -- no broadening there at all.
    """
    return [*scope_domains, f"*{INTERACTSH_SUFFIX}", *METADATA_HOSTS]


def build_call_target(scope_domains: list[str], *, transport: httpx.AsyncBaseTransport | None = None):
    """Builds one `call_target` closure for a single sandboxed
    execution, per Section 10.7's exact signature.

    Args:
        scope_domains: The program's in-scope domain patterns for this
            session. Captured by closure, not exposed as a parameter of
            the returned function -- the untrusted script must not be
            able to widen its own scope boundary by choosing a different
            value.
        transport: An `httpx.AsyncBaseTransport` to use instead of real
            network I/O, threaded through to each call's
            `InterceptingClient` exactly as `InterceptingClient`'s own
            constructor already supports (not blueprint-specified --
            the same standard httpx dependency-injection point that
            module's tests already use, added here so this module's
            tests can too, instead of requiring real network access).
            `None` (the default) means real network I/O.

    Returns:
        A synchronous `call_target(url, method="GET", headers=None,
        body=None) -> dict` function. Each call returns
        `{"status": int, "headers": dict, "body_preview": str}` (Section
        10.7, verbatim) on success. Raises `SandboxOutOfScopeError` if
        `url` fails `check_outbound` -- this propagates out of the
        untrusted script's `exec()` exactly like any other exception the
        script itself might raise; `code_executor.py` catches it at that
        level, not here.
    """
    shared_rate_limiter = RateLimiter()

    def call_target(
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> dict:
        if not check_outbound(url, scope_domains):
            raise SandboxOutOfScopeError(f"sandbox call_target blocked: {url}")

        async def _do_request() -> httpx.Response:
            client = RateLimitedClient(
                scope_domains=_augmented_scope_domains(scope_domains),
                intercepting_client=InterceptingClient(transport=transport),
                rate_limiter=shared_rate_limiter,
            )
            try:
                return await client.request(method, url, headers=headers, content=body)
            finally:
                await client.aclose()

        response = asyncio.run(_do_request())
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body_preview": response.text[:CALL_TARGET_BODY_PREVIEW_CHARS],
        }

    return call_target
