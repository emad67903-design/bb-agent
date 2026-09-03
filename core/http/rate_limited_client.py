"""
Implements: Section 3 -- core/http/rate_limited_client.py (wraps
`InterceptingClient` and `scope_enforcer`). Also implements Section
10.2 item 1 (RateLimitedClient as one of the four independent scope-
enforcement layers), Section 10.3 ("transport-layer hard limit; cannot
be bypassed by LLM decisions. Default: 10 req/sec/host"), and Section
4.4's `caller_id` mechanism ("carried on the scanner's RateLimitedClient
instance and passed down through InterceptingClient to every
is_allowed() call").
Blueprint: bb_agent_v6.6_final_blueprint.md

`OutOfScopeError` IS ITS OWN CLASS HERE, NOT IMPORTED FROM
`core.browser.browser_tool` -- a deliberate choice, not an oversight.
`browser_tool.py` (Week 3) already defines an `OutOfScopeError` with the
identical concept and an almost-identical message
(`f"BrowserTool blocked: {url}"`). Importing that class here would
create a dependency from the HTTP layer onto the browser layer for a
generic cross-cutting concept neither layer conceptually owns. Section
10.2 itself frames scope enforcement as FOUR INDEPENDENT layers
("Python HTTP client, Go services, Playwright entry point, and sandbox
`call_target()` all enforce scope independently") -- independent
enforcement, each with its own exception type following the same naming
convention (`Out Of Scope Error`, `"<ComponentName> blocked: {url}"`),
matches that stated independence better than centralizing the error
type would.

RATE LIMIT NUMBER IS A CONSTRUCTOR DEFAULT, NOT READ FROM `scope.yaml`
-- A CITED GAP (docs/DECISIONS.md, Week 5 section): Section 10.3 says
the 10 req/sec/host default is "configurable in scope.yaml," but no
section anywhere gives the actual YAML key name for it (contrast
`race_parallel: 30`, Section 3's `scope.yaml` comment block, which DOES
give an exact key). `requests_per_second` defaults to `10.0` (Section
10.3's literal number) as a constructor parameter; the scope.yaml-
reading wire-up is left for whenever a real config-loading layer exists
to read scope.yaml's other documented fields too, matching the
"framework now, wiring later" pattern already used elsewhere in this
project (e.g. `token_throttler.py`, docs/DECISIONS.md item 9).

ALGORITHM IS FIXED-MINIMUM-INTERVAL PER HOST, NOT A TOKEN BUCKET -- A
DOCUMENTED READING, NOT A CITATION: Section 10.3 gives only the number
(10 req/sec/host), never an algorithm. A fixed minimum interval between
consecutive requests to the same host (1 / rate seconds apart) is the
simplest rate limiter that satisfies "cannot be bypassed by LLM
decisions" -- it is a hard wait enforced in code, not a policy a caller
could reason around -- and it is directly, deterministically testable
(inject a fake clock/sleep function) without needing a burst-capacity
concept the blueprint never mentions.

`credential_validation_allowlist` NOW ACCEPTED AND CARRIED (docs/
DECISIONS.md item 96) -- A REAL, PRE-EXISTING GAP CLOSED, NOT A NEW
FEATURE INVENTED: `is_allowed()` (Section 4.4/R-H4) has always taken an
explicit `credential_validation_allowlist` parameter for its
`hardcoded_credentials.py` exemption, and this class's own `caller_id`
docstring already said the parameter existed "for the
`credential_validation_allowlist` exemption... reserved for Week 6" --
but `request()` never actually passed one to `is_allowed()`, so the
exemption was unreachable through this class regardless of `caller_id`
being correct. Found while building `hardcoded_credentials.py` (Week 7
Batch 4), the first caller that actually needs it -- the same
"anticipated in a docstring, never wired up, caught by the first real
caller" shape `is_allowed_outbound` turned out to have for
`ssrf_scanner.py` (item 83), except here the fix genuinely is needed
(unlike `is_allowed_outbound`, which turned out not to be, once
traced through). `None` remains the default, so every existing caller
of this class is unaffected -- confirmed by re-running every
pre-existing test in this file and every scanner's own test suite
unmodified before and after this change.
"""

from __future__ import annotations

import asyncio
import time
import urllib.parse
from typing import Awaitable, Callable

import httpx

from core.governance.scope_enforcer import is_allowed
from core.http.intercepting_client import InterceptingClient, TrafficLogStore
from core.ontology.scope import CredentialValidationAllowlist


class OutOfScopeError(Exception):
    """Raised when `RateLimitedClient` blocks a request whose host is not in scope."""


class RateLimiter:
    """Per-host fixed-minimum-interval rate limiter (Section 10.3).

    Args:
        requests_per_second: Requests/second permitted to any single
            host. Defaults to 10.0 (Section 10.3's literal number).
        sleep_fn: Awaitable sleep function, `(seconds) -> None`.
            Defaults to `asyncio.sleep`; overridable so tests can run
            without real wall-clock waiting.
        time_fn: Monotonic clock function, `() -> float`. Defaults to
            `time.monotonic`; overridable for deterministic tests.
    """

    def __init__(
        self,
        requests_per_second: float = 10.0,
        *,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._min_interval = 1.0 / requests_per_second
        self._last_request_time: dict[str, float] = {}
        self._sleep_fn = sleep_fn if sleep_fn is not None else asyncio.sleep
        self._time_fn = time_fn if time_fn is not None else time.monotonic

    async def wait_for_slot(self, host: str) -> None:
        """Blocks (via `sleep_fn`) until `host` may be sent another
        request without exceeding `requests_per_second`.

        Args:
            host: The destination hostname.
        """
        now = self._time_fn()
        last = self._last_request_time.get(host)
        if last is not None:
            wait_time = self._min_interval - (now - last)
            if wait_time > 0:
                await self._sleep_fn(wait_time)
                now = self._time_fn()
        self._last_request_time[host] = now


class RateLimitedClient:
    """Section 3/10.2/10.3's RateLimitedClient: the ONLY sanctioned way
    project code makes an outbound HTTP request (Engineering
    Constitution, "ONE HTTP LAYER, NO EXCEPTIONS"). Wraps
    `InterceptingClient` for traffic capture/suppression, enforces scope
    via `scope_enforcer.is_allowed` before every request, and rate-limits
    per host.

    Args:
        scope_domains: The program's in-scope domain patterns, already
            loaded (e.g. via
            `core.governance.scope_enforcer.load_scope_domains_for_enforcement`)
            -- matches `BrowserTool`'s identical constructor pattern
            (Week 3): callers load this once, not on every request
            (`is_allowed`'s own docstring explains why -- re-parsing
            YAML per call would reproduce the exact per-call-cost
            problem `caller_id` was introduced to avoid).
        caller_id: This client's identity for the `credential_validation_allowlist`
            exemption (Section 4.4). Set once at construction and
            carried for the client's lifetime -- never re-derived via
            call-stack inspection (Section 4.4's explicit rejection of
            that approach). `None` if this client has no special
            exemption (every caller other than `hardcoded_credentials.py`,
            per Section 4.4).
        intercepting_client: The `InterceptingClient` to wrap. Defaults
            to a fresh one.
        requests_per_second: Passed to the internal `RateLimiter` if
            `rate_limiter` is not supplied directly. Defaults to 10.0
            (Section 10.3).
        rate_limiter: A `RateLimiter` instance, for sharing one limiter
            (and its per-host state) across multiple `RateLimitedClient`
            instances, or for test injection. Defaults to a fresh
            `RateLimiter(requests_per_second)`.
        credential_validation_allowlist: The `credential_validation_
            allowlist` exemption data (Section 4.4/R-H4), normally
            loaded once via `core.governance.scope_config_generator.
            load_credential_validation_allowlist` and carried by the
            caller, mirroring how `scope_domains` itself is loaded once
            and carried. `None` (the default) means this client never
            offers the exemption -- correct for every caller except
            `hardcoded_credentials.py`'s own dedicated validation
            client (see that scanner's module docstring).
    """

    def __init__(
        self,
        *,
        scope_domains: list[str],
        caller_id: str | None = None,
        intercepting_client: InterceptingClient | None = None,
        requests_per_second: float = 10.0,
        rate_limiter: RateLimiter | None = None,
        credential_validation_allowlist: CredentialValidationAllowlist | None = None,
    ) -> None:
        self._scope_domains = scope_domains
        self._caller_id = caller_id
        self._client = intercepting_client if intercepting_client is not None else InterceptingClient()
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter(requests_per_second)
        self._credential_validation_allowlist = credential_validation_allowlist

    @property
    def caller_id(self) -> str | None:
        return self._caller_id

    @property
    def store(self) -> TrafficLogStore:
        """The wrapped `InterceptingClient`'s traffic log store."""
        return self._client.store

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: str | bytes | None = None,
    ) -> httpx.Response:
        """Scope-checks, rate-limits, then makes the request.

        Args:
            method: HTTP method.
            url: The request URL.
            headers: Request headers.
            content: Request body, if any.

        Returns:
            The `httpx.Response`.

        Raises:
            OutOfScopeError: If `url`'s host is not in scope (Section
                10.2: hard gate, checked before every request).
        """
        if not is_allowed(
            url,
            self._scope_domains,
            caller_id=self._caller_id,
            credential_validation_allowlist=self._credential_validation_allowlist,
        ):
            raise OutOfScopeError(f"RateLimitedClient blocked: {url}")

        host = urllib.parse.urlparse(url).hostname or ""
        await self._rate_limiter.wait_for_slot(host)

        return await self._client.request(method, url, headers=headers, content=content)

    async def aclose(self) -> None:
        """Closes the wrapped `InterceptingClient`."""
        await self._client.aclose()

    async def __aenter__(self) -> RateLimitedClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
