"""
Implements: Section 10.7 / Section 4.4 test coverage --
core/sandbox/safety_guard.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import httpx
import pytest

from core.sandbox.safety_guard import (
    METADATA_HOSTS,
    SandboxOutOfScopeError,
    build_call_target,
    check_outbound,
    is_allowed_outbound,
)


class TestIsAllowedOutbound:
    """Section 4.4's is_allowed_outbound, transcribed verbatim."""

    def test_in_scope_host_allowed(self):
        assert is_allowed_outbound("api.example.com", None, ["*.example.com"]) is True

    def test_interactsh_subdomain_allowed(self):
        assert is_allowed_outbound("xbow123.interactsh.com", None, []) is True

    def test_bare_interactsh_domain_not_allowed_by_this_branch(self):
        """Section 4.4's own code is `dst_host.endswith('.interactsh.com')`
        -- deliberately excludes the bare domain itself, unlike a
        *.interactsh.com wildcard scope-domain entry would. Transcribed
        exactly, including this asymmetry."""
        assert is_allowed_outbound("interactsh.com", None, []) is False

    def test_metadata_hostname_allowed(self):
        for host in METADATA_HOSTS:
            assert is_allowed_outbound(host, None, []) is True

    def test_metadata_ip_literal_as_host_allowed(self):
        assert is_allowed_outbound("169.254.169.254", None, []) is True

    def test_dst_ip_resolving_to_metadata_ip_allowed(self):
        """The DNS-rebinding case: a hostname that is not itself a known
        metadata host, but resolves to the metadata IP."""
        assert is_allowed_outbound("innocuous-looking.example", "169.254.169.254", []) is True

    def test_unrelated_host_denied(self):
        assert is_allowed_outbound("evil.com", None, ["*.example.com"]) is False

    def test_none_host_fails_closed(self):
        assert is_allowed_outbound(None, None, ["*.example.com"]) is False

    def test_none_dst_ip_does_not_crash_and_does_not_grant(self):
        assert is_allowed_outbound("evil.com", None, ["*.example.com"]) is False


class TestCheckOutbound:
    """The URL-parsing + DNS-resolving wrapper around is_allowed_outbound."""

    def test_in_scope_url(self):
        assert check_outbound("https://api.example.com/x", ["*.example.com"]) is True

    def test_out_of_scope_url(self):
        assert check_outbound("https://evil.com/x", ["*.example.com"]) is False

    def test_interactsh_url(self):
        assert check_outbound("https://abc123.interactsh.com/probe", []) is True

    def test_metadata_ip_url(self):
        assert check_outbound("http://169.254.169.254/latest/meta-data/ami-id", []) is True

    def test_unparseable_url_fails_closed(self):
        assert check_outbound("not a url at all", ["*.example.com"]) is False


class TestBuildCallTarget:
    """The Section 10.7 call_target() closure, built once per sandboxed
    execution and exercised with an injected transport (no real network
    access -- mirrors InterceptingClient's own established test pattern)."""

    @pytest.fixture
    def mock_transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "169.254.169.254" in str(request.url):
                return httpx.Response(200, text="ami-98765", headers={"X-Meta": "1"})
            return httpx.Response(200, json={"ok": True}, headers={"X-Real": "1"})

        return httpx.MockTransport(handler)

    def test_returns_exact_section_10_7_shape(self, mock_transport):
        ct = build_call_target(["*.example.com"], transport=mock_transport)
        result = ct("https://api.example.com/test")
        assert set(result.keys()) == {"status", "headers", "body_preview"}
        assert result["status"] == 200
        assert isinstance(result["headers"], dict)
        assert isinstance(result["body_preview"], str)

    def test_in_scope_call_succeeds(self, mock_transport):
        ct = build_call_target(["*.example.com"], transport=mock_transport)
        result = ct("https://api.example.com/test")
        assert result["status"] == 200
        assert result["body_preview"] == '{"ok":true}'

    def test_metadata_call_succeeds_despite_not_being_in_scope_domains(self, mock_transport):
        """The composition case that failed on first implementation,
        caught by a pre-commit smoke test: check_outbound() approves the
        metadata IP, but RateLimitedClient's own independent scope check
        does not know about that exception unless it is also told."""
        ct = build_call_target(["*.example.com"], transport=mock_transport)
        result = ct("http://169.254.169.254/latest/meta-data/ami-id")
        assert result["status"] == 200
        assert result["body_preview"] == "ami-98765"

    def test_out_of_scope_call_raises(self, mock_transport):
        ct = build_call_target(["*.example.com"], transport=mock_transport)
        with pytest.raises(SandboxOutOfScopeError, match="totally-unrelated-evil.com"):
            ct("https://totally-unrelated-evil.com/")

    def test_out_of_scope_call_never_reaches_the_transport(self):
        """An out-of-scope call must be rejected before any request is
        attempted, not merely fail late -- assert the transport is never
        invoked at all."""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200)

        ct = build_call_target(["*.example.com"], transport=httpx.MockTransport(handler))
        with pytest.raises(SandboxOutOfScopeError):
            ct("https://evil.com/")
        assert calls == []

    def test_body_preview_truncated_to_512_chars(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="X" * 1000)

        ct = build_call_target(["*.example.com"], transport=httpx.MockTransport(handler))
        result = ct("https://api.example.com/big")
        assert len(result["body_preview"]) == 512
        assert result["body_preview"] == "X" * 512

    def test_short_body_not_padded(self, mock_transport):
        ct = build_call_target(["*.example.com"], transport=mock_transport)
        result = ct("https://api.example.com/test")
        assert result["body_preview"] == '{"ok":true}'
        assert len(result["body_preview"]) < 512

    def test_default_method_is_get(self, mock_transport):
        seen_methods = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_methods.append(request.method)
            return httpx.Response(200)

        ct = build_call_target(["*.example.com"], transport=httpx.MockTransport(handler))
        ct("https://api.example.com/test")
        assert seen_methods == ["GET"]

    def test_explicit_method_and_headers_and_body_pass_through(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["header"] = request.headers.get("x-custom")
            seen["body"] = request.content.decode()
            return httpx.Response(200)

        ct = build_call_target(["*.example.com"], transport=httpx.MockTransport(handler))
        ct(
            "https://api.example.com/test",
            method="POST",
            headers={"X-Custom": "abc"},
            body="payload-data",
        )
        assert seen == {"method": "POST", "header": "abc", "body": "payload-data"}

    def test_rate_limiting_persists_across_calls_within_one_closure(self):
        """The shared RateLimiter must serialize timing across separate
        asyncio.run() calls, not reset per call -- verified with a fake
        clock/sleep so the test does not need real wall-clock waiting."""
        from core.sandbox import safety_guard as sg_module

        recorded_waits = []

        # Patch RateLimiter's defaults are 10 req/s; instead of waiting
        # 0.1s real time per test, inject a fast, deterministic
        # sleep/clock pair the same way RateLimiter's own tests do.
        import core.http.rate_limited_client as rlc_module

        fake_time = [0.0]

        def fake_time_fn():
            return fake_time[0]

        async def fake_sleep(seconds):
            recorded_waits.append(seconds)
            fake_time[0] += seconds

        original_rate_limiter_init = rlc_module.RateLimiter.__init__

        def patched_init(self, requests_per_second: float = 10.0, *, sleep_fn=None, time_fn=None):
            original_rate_limiter_init(
                self,
                requests_per_second,
                sleep_fn=fake_sleep,
                time_fn=fake_time_fn,
            )

        rlc_module.RateLimiter.__init__ = patched_init
        try:

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200)

            ct = build_call_target(["*.example.com"], transport=httpx.MockTransport(handler))
            ct("https://api.example.com/a")
            ct("https://api.example.com/a")
            ct("https://api.example.com/a")
        finally:
            rlc_module.RateLimiter.__init__ = original_rate_limiter_init

        # First call: no prior request recorded, no wait. Calls 2 and 3:
        # same host, so the shared limiter should have recorded a wait
        # each time (proving it remembered call 1 and call 2's timing).
        assert len(recorded_waits) == 2

    def test_scope_domains_captured_at_construction_not_a_call_parameter(self, mock_transport):
        """The untrusted script's call_target(url, method, headers, body)
        signature has no scope_domains parameter at all (Section 10.7) --
        confirms it cannot be passed positionally or by keyword to widen
        scope."""
        ct = build_call_target(["*.example.com"], transport=mock_transport)
        with pytest.raises(TypeError):
            ct("https://api.example.com/test", scope_domains=["*.evil.com"])
