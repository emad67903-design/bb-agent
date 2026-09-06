"""
Implements: Section 10.2/10.3 test coverage --
core/http/rate_limited_client.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import OutOfScopeError, RateLimitedClient, RateLimiter
from core.ontology.scope import CredentialValidationAllowlist


class _FakeClock:
    """Deterministic, manually-advanced clock + no-op-but-recording sleep,
    so RateLimiter's timing logic can be tested without real wall-clock
    delays or timing flakiness."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleep_calls: list[float] = []

    def time_fn(self) -> float:
        return self.now

    async def sleep_fn(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds  # simulate time passing during the sleep


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_first_request_to_a_host_never_waits(self):
        clock = _FakeClock()
        limiter = RateLimiter(requests_per_second=10.0, sleep_fn=clock.sleep_fn, time_fn=clock.time_fn)
        await limiter.wait_for_slot("example.com")
        assert clock.sleep_calls == []

    @pytest.mark.asyncio
    async def test_second_request_within_interval_waits_the_remainder(self):
        clock = _FakeClock()
        limiter = RateLimiter(requests_per_second=10.0, sleep_fn=clock.sleep_fn, time_fn=clock.time_fn)  # 100ms interval
        await limiter.wait_for_slot("example.com")
        clock.now += 0.03  # only 30ms elapsed
        await limiter.wait_for_slot("example.com")
        assert clock.sleep_calls == pytest.approx([0.07])  # waits the remaining 70ms

    @pytest.mark.asyncio
    async def test_request_after_interval_has_elapsed_does_not_wait(self):
        clock = _FakeClock()
        limiter = RateLimiter(requests_per_second=10.0, sleep_fn=clock.sleep_fn, time_fn=clock.time_fn)
        await limiter.wait_for_slot("example.com")
        clock.now += 0.5  # well over the 100ms interval
        await limiter.wait_for_slot("example.com")
        assert clock.sleep_calls == []

    @pytest.mark.asyncio
    async def test_different_hosts_tracked_independently(self):
        clock = _FakeClock()
        limiter = RateLimiter(requests_per_second=10.0, sleep_fn=clock.sleep_fn, time_fn=clock.time_fn)
        await limiter.wait_for_slot("a.com")
        await limiter.wait_for_slot("b.com")  # different host, same instant -- no wait expected
        assert clock.sleep_calls == []

    @pytest.mark.asyncio
    async def test_higher_rate_gives_shorter_interval(self):
        clock = _FakeClock()
        limiter = RateLimiter(requests_per_second=100.0, sleep_fn=clock.sleep_fn, time_fn=clock.time_fn)  # 10ms interval
        await limiter.wait_for_slot("example.com")
        clock.now += 0.005  # 5ms elapsed
        await limiter.wait_for_slot("example.com")
        assert clock.sleep_calls == pytest.approx([0.005])  # remaining 5ms

    @pytest.mark.asyncio
    async def test_default_rate_is_ten_per_second(self):
        clock = _FakeClock()
        limiter = RateLimiter(sleep_fn=clock.sleep_fn, time_fn=clock.time_fn)
        await limiter.wait_for_slot("example.com")
        clock.now += 0.01
        await limiter.wait_for_slot("example.com")
        assert clock.sleep_calls == pytest.approx([0.09])  # 100ms interval - 10ms elapsed


class TestRateLimitedClient:
    def _client(
        self, *, scope_domains, caller_id=None, handler=None, rate_limiter=None, credential_validation_allowlist=None
    ) -> RateLimitedClient:
        transport = httpx.MockTransport(handler or (lambda r: httpx.Response(200, text="ok")))
        return RateLimitedClient(
            scope_domains=scope_domains, caller_id=caller_id,
            intercepting_client=InterceptingClient(transport=transport),
            rate_limiter=rate_limiter or RateLimiter(requests_per_second=10_000.0),  # fast, avoids real waits
            credential_validation_allowlist=credential_validation_allowlist,
        )

    @pytest.mark.asyncio
    async def test_in_scope_request_succeeds(self):
        client = self._client(scope_domains=["example.com"])
        response = await client.request("GET", "http://example.com/page")
        assert response.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_out_of_scope_request_raises(self):
        client = self._client(scope_domains=["example.com"])
        with pytest.raises(OutOfScopeError, match="RateLimitedClient blocked"):
            await client.request("GET", "http://evil.com/page")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_wildcard_scope_domain_matches_subdomain(self):
        client = self._client(scope_domains=["*.example.com"])
        response = await client.request("GET", "http://sub.example.com/page")
        assert response.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_scope_domains_blocks_everything(self):
        """Fails closed: no configured scope means nothing is in scope."""
        client = self._client(scope_domains=[])
        with pytest.raises(OutOfScopeError):
            await client.request("GET", "http://example.com/page")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_out_of_scope_request_never_reaches_the_network(self):
        """The scope check must happen BEFORE the request is dispatched --
        confirmed by asserting no traffic entry was logged for the
        blocked attempt."""
        client = self._client(scope_domains=["example.com"])
        with pytest.raises(OutOfScopeError):
            await client.request("GET", "http://evil.com/page")
        assert client.store.entry_count == 0
        await client.aclose()

    def test_caller_id_exposed_and_set_once_at_construction(self):
        client = self._client(scope_domains=["example.com"], caller_id="xss_scanner")
        assert client.caller_id == "xss_scanner"

    def test_caller_id_defaults_to_none(self):
        client = self._client(scope_domains=["example.com"])
        assert client.caller_id is None

    @pytest.mark.asyncio
    async def test_response_is_the_real_httpx_response(self):
        client = self._client(scope_domains=["example.com"], handler=lambda r: httpx.Response(201, json={"created": True}))
        response = await client.request("POST", "http://example.com/create")
        assert response.status_code == 201
        assert response.json() == {"created": True}
        await client.aclose()

    @pytest.mark.asyncio
    async def test_request_is_logged_via_the_wrapped_intercepting_client(self):
        client = self._client(scope_domains=["example.com"])
        await client.request("GET", "http://example.com/page")
        assert client.store.entry_count == 1
        assert client.store.entries[0].url == "http://example.com/page"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_rate_limiter_is_actually_consulted(self):
        """Confirms wiring, not RateLimiter's own logic (already tested
        above): a rate limiter with an artificially huge interval should
        cause wait_for_slot to be invoked (observable via a spy)."""
        calls: list[str] = []

        class _SpyLimiter:
            async def wait_for_slot(self, host: str) -> None:
                calls.append(host)

        client = self._client(scope_domains=["example.com"], rate_limiter=_SpyLimiter())
        await client.request("GET", "http://example.com/page")
        assert calls == ["example.com"]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_async_context_manager_closes_client(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
        async with RateLimitedClient(
            scope_domains=["example.com"],
            intercepting_client=InterceptingClient(transport=transport),
            rate_limiter=RateLimiter(requests_per_second=10_000.0),
        ) as client:
            response = await client.request("GET", "http://example.com/page")
            assert response.status_code == 200


class TestRateLimitedClientCredentialValidationAllowlist:
    """docs/DECISIONS.md item 96: `credential_validation_allowlist` is
    now accepted and actually threaded through to `is_allowed()` --
    previously accepted nowhere in this class despite `caller_id`'s own
    docstring already describing the exemption it enables."""

    def _client(
        self, *, scope_domains, caller_id=None, handler=None, credential_validation_allowlist=None
    ) -> RateLimitedClient:
        transport = httpx.MockTransport(handler or (lambda r: httpx.Response(200, text="ok")))
        return RateLimitedClient(
            scope_domains=scope_domains,
            caller_id=caller_id,
            intercepting_client=InterceptingClient(transport=transport),
            rate_limiter=RateLimiter(requests_per_second=10_000.0),
            credential_validation_allowlist=credential_validation_allowlist,
        )

    @pytest.mark.asyncio
    async def test_correct_caller_id_and_enabled_allowlist_permits_out_of_scope_host(self):
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        client = self._client(
            scope_domains=["example.com"],  # api.stripe.com is NOT in here
            caller_id="hardcoded_credentials",
            credential_validation_allowlist=allowlist,
        )
        response = await client.request("GET", "https://api.stripe.com/v1/account")
        assert response.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_wrong_caller_id_still_blocked_even_with_enabled_allowlist(self):
        """The exemption is scanner-specific -- an allowlist alone,
        without the matching caller_id, grants nothing."""
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        client = self._client(
            scope_domains=["example.com"],
            caller_id="xss_scanner",  # NOT hardcoded_credentials
            credential_validation_allowlist=allowlist,
        )
        with pytest.raises(OutOfScopeError):
            await client.request("GET", "https://api.stripe.com/v1/account")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_correct_caller_id_but_disabled_allowlist_blocked(self):
        allowlist = CredentialValidationAllowlist(enabled=False, external_apis=["api.stripe.com"])
        client = self._client(
            scope_domains=["example.com"],
            caller_id="hardcoded_credentials",
            credential_validation_allowlist=allowlist,
        )
        with pytest.raises(OutOfScopeError):
            await client.request("GET", "https://api.stripe.com/v1/account")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_correct_caller_id_but_host_not_in_external_apis_blocked(self):
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        client = self._client(
            scope_domains=["example.com"],
            caller_id="hardcoded_credentials",
            credential_validation_allowlist=allowlist,
        )
        with pytest.raises(OutOfScopeError):
            await client.request("GET", "https://not-on-the-allowlist.example.com")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_no_allowlist_provided_defaults_to_none_and_blocks(self):
        """Default None -- confirms every other scanner's existing
        behavior (no allowlist ever passed) is unaffected."""
        client = self._client(scope_domains=["example.com"], caller_id="hardcoded_credentials")
        with pytest.raises(OutOfScopeError):
            await client.request("GET", "https://api.stripe.com/v1/account")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_in_scope_host_unaffected_by_allowlist_presence(self):
        """The allowlist only ever WIDENS access to specific external
        hosts -- it must never narrow or otherwise affect normal
        in-scope requests."""
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        client = self._client(
            scope_domains=["example.com"],
            caller_id="hardcoded_credentials",
            credential_validation_allowlist=allowlist,
        )
        response = await client.request("GET", "https://example.com/normal-target-request")
        assert response.status_code == 200
        await client.aclose()
