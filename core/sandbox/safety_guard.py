"""
Implements: Section 3 -- core/sandbox/safety_guard.py ("Outbound block
except interactsh + metadata + scope"), and Section 10.7's pre-approved
`call_target()`:
    def call_target(url: str, method: str = "GET",
                    headers: dict = None, body: str = None) -> dict:
        '''Scope-checked, rate-limited. Returns {"status": int,
        "headers": dict, "body_preview": str (512 chars)}'''
Blueprint: bb_agent_v6.6_final_blueprint.md

WHERE "interactsh + metadata + scope" COMES FROM (docs/DECISIONS.md item
66): Section 3's one-line comment for this file is not a free-standing
spec -- it is describing the exact same three-way policy Section 4.4
already writes out in full, in a function of that name:

    METADATA_HOSTS = frozenset({'169.254.169.254', 'metadata.google.internal'})
    def is_allowed_outbound(dst_host: str, dst_ip: str, scope_domains: set[str]) -> bool:
        return (
            dst_host.endswith('.interactsh.com')
            or dst_host in METADATA_HOSTS
            or dst_ip == '169.254.169.254'
            or _is_scope_allowed(dst_host, scope_domains)
        )

Section 4.4 titles that block "Python HTTP layer (scope_enforcer.py +
intercepting_client.py)" -- a different call site than this file's own
(the sandbox's `call_target()`) -- but the POLICY itself ("what is this
codebase willing to let something call out to, beyond its own
scope_domains") is the same policy by construction: `call_target()`
needs the identical exceptions `is_allowed_outbound` already grants
(interactsh, for OOB vulnerability confirmation; the cloud metadata
endpoints, for SSRF PoC verification -- Section 7.3's whole IMDSv2 probe
matrix is exactly this: an in-scope-app SSRF bug being confirmed by
reaching an out-of-scope metadata IP on purpose). `is_allowed_outbound`
itself was never actually built anywhere in this codebase before this
week (grep-confirmed against `core/http/intercepting_client.py`, its
would-be Week-5 home per Section 4.4's own text -- it references
`scope_enforcer.py` in one docstring sentence and calls nothing from
it). This file is that function's first real implementation, scoped to
its first real caller.

`_is_scope_allowed` IS IMPORTED FROM `scope_enforcer.py`, NOT
REIMPLEMENTED: the wildcard-matching algorithm is the SAME algorithm
Week 3 already wrote, tested, and Section 4.4 defines once. Reusing it
here is not a second, drifting copy of the logic -- it is the "scope"
third of "interactsh + metadata + scope", literally the same scope. What
IS independent, per Section 10.2's "four independent layers" framing, is
the ENFORCEMENT CALL SITE: this file makes its own decision, at its own
point in the code, and nothing about the sandbox depends on
`browser_tool.py`, `RateLimitedClient`, or the Go services being correct
for `call_target()` to also be correct.

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

from core.governance.scope_enforcer import _is_scope_allowed
from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient, RateLimiter

INTERACTSH_SUFFIX = ".interactsh.com"
METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
METADATA_IP = "169.254.169.254"

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


def is_allowed_outbound(dst_host: str | None, dst_ip: str | None, scope_domains: list[str]) -> bool:
    """Section 4.4's `is_allowed_outbound`, verbatim (module docstring
    explains why this file, not `scope_enforcer.py`, is its first real
    caller).

    Args:
        dst_host: The destination URL's hostname, or `None` if it could
            not be parsed (fails closed -- see Returns).
        dst_ip: The destination hostname's resolved IP, or `None` if
            resolution failed or was not attempted.
        scope_domains: The program's in-scope domain patterns.

    Returns:
        `True` if `dst_host` is an interactsh subdomain, a known cloud
        metadata hostname, resolves to the metadata IP, or is in scope
        (via `scope_enforcer._is_scope_allowed`'s wildcard matching).
        `False` (fail closed) if `dst_host` is `None`.
    """
    if dst_host is None:
        return False
    return (
        dst_host.endswith(INTERACTSH_SUFFIX)
        or dst_host in METADATA_HOSTS
        or dst_ip == METADATA_IP
        or _is_scope_allowed(dst_host, scope_domains)
    )


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
