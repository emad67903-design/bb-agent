"""
Implements: test coverage for core/http/interactsh_client.py
(Section 4.2/4.3, docs/DECISIONS.md item 81).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from core.governance.scope_enforcer import INTERACTSH_SUFFIX
from core.http.intercepting_client import InterceptingClient
from core.http.interactsh_client import (
    EARLY_PHASE_DURATION_SECONDS,
    POLL_INTERVAL_EARLY_SECONDS,
    POLL_INTERVAL_LATE_SECONDS,
    PUBLIC_CONSECUTIVE_429_THRESHOLD,
    TOTAL_TIMEOUT_SECONDS,
    InteractshClient,
    InteractshRateLimited,
    _default_poll_check,
)
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.enums import InteractshMode, OOBPollOutcome


def _no_wait_sleep(_seconds: float) -> "asyncio.Future":
    return asyncio.sleep(0)


def _session(handler) -> RateLimitedClient:
    """Scoped to `*.interactsh.com` -- matches the module docstring's
    documented caller contract exactly."""
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["*.interactsh.com"],
        caller_id="interactsh_client",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _client(handler, **kwargs) -> InteractshClient:
    kwargs.setdefault("sleep_fn", _no_wait_sleep)
    return InteractshClient(_session(handler), session_id="sess123", **kwargs)


class TestRegisterProbe:
    def test_format_matches_section_4_2_exactly(self):
        client = _client(lambda r: httpx.Response(200, text=""))
        url = client.register_probe()
        assert url.startswith("XBOW_sess123_")
        assert url.endswith(INTERACTSH_SUFFIX)

    def test_correlation_id_embeds_session_id(self):
        client = _client(lambda r: httpx.Response(200, text=""))
        url = client.register_probe()
        correlation_id = url.removesuffix(INTERACTSH_SUFFIX)
        assert correlation_id.startswith("XBOW_sess123_")

    def test_two_calls_produce_different_nonces(self):
        client = _client(lambda r: httpx.Response(200, text=""))
        assert client.register_probe() != client.register_probe()

    def test_initial_mode_is_public(self):
        client = _client(lambda r: httpx.Response(200, text=""))
        assert client.mode is InteractshMode.PUBLIC


class TestPollReceived:
    @pytest.mark.asyncio
    async def test_received_on_first_poll(self):
        client = _client(lambda r: httpx.Response(200, text="interaction-logged"))
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.RECEIVED

    @pytest.mark.asyncio
    async def test_received_after_several_empty_polls(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="hit" if call_count >= 3 else "")

        client = _client(handler)
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.RECEIVED
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_mode_unaffected_by_a_successful_poll(self):
        client = _client(lambda r: httpx.Response(200, text="hit"))
        url = client.register_probe()
        await client.poll(url)
        assert client.mode is InteractshMode.PUBLIC


class TestPollUnavailableTimeout:
    @pytest.mark.asyncio
    async def test_no_callback_within_5_min_returns_unavailable(self):
        client = _client(lambda r: httpx.Response(200, text=""))
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_mode_stays_public_on_a_clean_timeout(self):
        """A probe that simply never fires is not the interactsh
        SERVICE failing -- module docstring's distinction between
        InteractshMode (session-wide) and OOBPollOutcome (per-probe)."""
        client = _client(lambda r: httpx.Response(200, text=""))
        url = client.register_probe()
        await client.poll(url)
        assert client.mode is InteractshMode.PUBLIC

    @pytest.mark.asyncio
    async def test_exact_poll_schedule_15s_x8_then_60s_x3(self):
        intervals = []
        client = _client(lambda r: httpx.Response(200, text=""))

        async def tracking_sleep(seconds):
            intervals.append(seconds)

        client._sleep_fn = tracking_sleep
        url = client.register_probe()
        await client.poll(url)

        assert intervals == [POLL_INTERVAL_EARLY_SECONDS] * 8 + [POLL_INTERVAL_LATE_SECONDS] * 3
        assert sum(intervals) == TOTAL_TIMEOUT_SECONDS
        assert EARLY_PHASE_DURATION_SECONDS == 8 * POLL_INTERVAL_EARLY_SECONDS


class TestPollAlreadyUnavailable:
    @pytest.mark.asyncio
    async def test_short_circuits_with_zero_requests(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, text="hit")

        client = _client(handler)
        client.mode = InteractshMode.UNAVAILABLE
        url = client.register_probe()

        assert await client.poll(url) is OOBPollOutcome.UNAVAILABLE
        assert calls == []


class TestPoll429Cascade:
    @pytest.mark.asyncio
    async def test_three_consecutive_429s_with_no_self_hosted_fn_goes_unavailable(self):
        client = _client(lambda r: httpx.Response(429, text=""))
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.UNAVAILABLE
        assert client.mode is InteractshMode.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_loop_stops_immediately_once_429x3_lands_on_unavailable(self):
        """No 4th/5th request after the cascade lands on UNAVAILABLE --
        the top-of-loop mode check, not just the eventual timeout."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, text="")

        client = _client(handler)
        url = client.register_probe()
        await client.poll(url)
        assert call_count == PUBLIC_CONSECUTIVE_429_THRESHOLD

    @pytest.mark.asyncio
    async def test_self_hosted_start_fn_succeeding_switches_mode_and_continues(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429 if call_count <= 3 else 200, text="hit")

        client = _client(handler, self_hosted_start_fn=lambda: True)
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.RECEIVED
        assert client.mode is InteractshMode.SELF_HOSTED

    @pytest.mark.asyncio
    async def test_self_hosted_start_fn_returning_false_goes_unavailable(self):
        client = _client(lambda r: httpx.Response(429, text=""), self_hosted_start_fn=lambda: False)
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.UNAVAILABLE
        assert client.mode is InteractshMode.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_self_hosted_start_fn_raising_is_caught_and_goes_unavailable(self):
        """Boundary to a caller-supplied callable -- must not let an
        arbitrary exception type escape poll()."""

        def broken_starter():
            raise RuntimeError("no Go binary on this machine")

        client = _client(lambda r: httpx.Response(429, text=""), self_hosted_start_fn=broken_starter)
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.UNAVAILABLE
        assert client.mode is InteractshMode.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_429_streak_resets_on_a_clean_response(self):
        """2 429s, then a clean miss, then 2 more 429s must NOT trigger
        the cascade -- "consecutive" per Section 4.3's own wording."""
        responses = [429, 429, 200, 429, 429, 200]
        call_count = 0

        def handler(request):
            nonlocal call_count
            status = responses[call_count] if call_count < len(responses) else 200
            call_count += 1
            return httpx.Response(status, text="hit" if status == 200 and call_count == len(responses) else "")

        client = _client(handler)
        url = client.register_probe()
        result = await client.poll(url)
        assert client.mode is InteractshMode.PUBLIC  # never reached 3 consecutive
        assert result is OOBPollOutcome.RECEIVED

    @pytest.mark.asyncio
    async def test_429_while_already_self_hosted_is_ignored_not_a_crash(self):
        """Section 4.3 only defines 429 behavior for the PUBLIC path --
        _handle_429 must not attempt a second transition or raise."""
        client = _client(lambda r: httpx.Response(429, text=""))
        client.mode = InteractshMode.SELF_HOSTED
        url = client.register_probe()
        result = await client.poll(url)  # must not raise
        assert result is OOBPollOutcome.UNAVAILABLE  # times out, still SELF_HOSTED throughout
        assert client.mode is InteractshMode.SELF_HOSTED


class TestPollEnvDependent:
    @pytest.mark.asyncio
    async def test_transport_error_returns_env_dependent(self):
        def handler(request):
            raise httpx.ConnectError("network unreachable", request=request)

        client = _client(handler)
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.ENV_DEPENDENT

    @pytest.mark.asyncio
    async def test_mode_unaffected_by_a_transport_error(self):
        """A network partition is per-probe (Section 4.3: "queue for
        retest"), not necessarily the whole session's connectivity --
        does not itself degrade InteractshMode."""

        def handler(request):
            raise httpx.ConnectError("network unreachable", request=request)

        client = _client(handler)
        url = client.register_probe()
        await client.poll(url)
        assert client.mode is InteractshMode.PUBLIC

    @pytest.mark.asyncio
    async def test_transport_error_stops_polling_immediately(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("network unreachable", request=request)

        client = _client(handler)
        url = client.register_probe()
        await client.poll(url)
        assert call_count == 1


class TestDefaultPollCheck:
    @pytest.mark.asyncio
    async def test_non_empty_200_body_is_received(self):
        session = _session(lambda r: httpx.Response(200, text="logged-interaction"))
        assert await _default_poll_check(session, "XBOW_s1_abc") is True

    @pytest.mark.asyncio
    async def test_empty_200_body_is_not_received(self):
        session = _session(lambda r: httpx.Response(200, text=""))
        assert await _default_poll_check(session, "XBOW_s1_abc") is False

    @pytest.mark.asyncio
    async def test_whitespace_only_200_body_is_not_received(self):
        session = _session(lambda r: httpx.Response(200, text="   \n  "))
        assert await _default_poll_check(session, "XBOW_s1_abc") is False

    @pytest.mark.asyncio
    async def test_404_is_not_received(self):
        session = _session(lambda r: httpx.Response(404, text=""))
        assert await _default_poll_check(session, "XBOW_s1_abc") is False

    @pytest.mark.asyncio
    async def test_429_raises_interactsh_rate_limited(self):
        session = _session(lambda r: httpx.Response(429, text=""))
        with pytest.raises(InteractshRateLimited):
            await _default_poll_check(session, "XBOW_s1_abc")

    @pytest.mark.asyncio
    async def test_requests_the_correlation_id_subdomain(self):
        """Case-insensitive comparison, deliberately: hostnames are
        case-insensitive per RFC 3986, and httpx correctly lowercases
        the subdomain when constructing the actual request -- confirmed
        by first writing this test with an exact-case assertion and
        watching it fail on real (not mocked-away) URL construction, not
        assumed. This is correct httpx/DNS behavior, not a bug -- see
        this test class's own module docstring note on why exact-case
        correlation matching, if ever needed by a real integration,
        belongs in a query parameter or path segment, not the poll
        request's hostname."""
        seen_urls = []

        def handler(request):
            seen_urls.append(str(request.url))
            return httpx.Response(200, text="")

        session = _session(handler)
        await _default_poll_check(session, "XBOW_s1_abc")
        assert seen_urls[0].lower().startswith(f"https://xbow_s1_abc{INTERACTSH_SUFFIX}".lower())


class TestPollCheckFnInjection:
    @pytest.mark.asyncio
    async def test_custom_poll_check_fn_is_used_instead_of_default(self):
        async def always_received(session, correlation_id):
            return True

        client = _client(lambda r: httpx.Response(200, text=""), poll_check_fn=always_received)
        url = client.register_probe()
        assert await client.poll(url) is OOBPollOutcome.RECEIVED

    @pytest.mark.asyncio
    async def test_custom_poll_check_fn_receives_the_bare_correlation_id_not_the_full_url(self):
        seen = []

        async def capturing(session, correlation_id):
            seen.append(correlation_id)
            return False

        client = _client(lambda r: httpx.Response(200, text=""), poll_check_fn=capturing, sleep_fn=_no_wait_sleep)
        url = client.register_probe()
        # only need one iteration's worth of evidence -- force an early exit via a received flag after first call
        client._time_fn = lambda: 0.0

        async def capturing_then_stop(session, correlation_id):
            seen.append(correlation_id)
            return True

        client._poll_check_fn = capturing_then_stop
        await client.poll(url)
        assert seen[0] == url.removesuffix(INTERACTSH_SUFFIX)
        assert INTERACTSH_SUFFIX not in seen[0]
