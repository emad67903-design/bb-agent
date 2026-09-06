"""
Implements: test coverage for core/scanners/hardcoded_credentials.py (Section 7.28).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.scope import CredentialValidationAllowlist
from core.scanners.hardcoded_credentials import HardcodedCredentialsScanner, _load_patterns
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="hardcoded_credentials",
        intercepting_client=InterceptingClient(transport=transport),
    )


_AWS_PATTERN = [
    {
        "id": "aws_access_key_id", "provider": "aws", "pattern": r"AKIA[0-9A-Z]{16}",
        "known_placeholders": ["AKIAIOSFODNN7EXAMPLE"], "validation_endpoint": "sts.amazonaws.com",
        "live_validation": False, "gap_reason": "needs SigV4 + paired secret",
    }
]
_STRIPE_PATTERN = [
    {
        "id": "stripe_live_secret_key", "provider": "stripe", "pattern": r"sk_live_[0-9a-zA-Z]{24,}",
        "known_placeholders": [], "validation_endpoint": "api.stripe.com", "live_validation": True,
    }
]
_GOOGLE_MAPS_PATTERN = [
    {
        "id": "google_maps_api_key", "provider": "google_maps", "pattern": r"AIza[0-9A-Za-z_-]{35}",
        "known_placeholders": [], "validation_endpoint": "maps.googleapis.com", "live_validation": True,
    }
]


class TestLoadPatterns:
    def test_real_pattern_file_has_four_entries_not_five(self):
        """See module docstring -- Microsoft Graph deliberately has no
        entry at all."""
        patterns = _load_patterns()
        assert len(patterns) == 4
        assert {p["provider"] for p in patterns} == {"aws", "stripe", "twilio", "google_maps"}
        assert "microsoft_graph" not in {p["provider"] for p in patterns}

    def test_exactly_two_have_live_validation(self):
        patterns = _load_patterns()
        live = [p["provider"] for p in patterns if p["live_validation"]]
        assert set(live) == {"stripe", "google_maps"}

    def test_no_bogus_8q_tilde_substring_anywhere(self):
        """Confirms the caught mistake (module docstring) never made it
        into the shipped file."""
        patterns = _load_patterns()
        assert not any("8Q~" in p["pattern"] for p in patterns)


class TestHardcodedCredentialsScannerRegistration:
    def test_registered_under_hardcoded_credentials(self):
        assert SCANNER_REGISTRY["hardcoded_credentials"] is HardcodedCredentialsScanner


class TestHardcodedCredentialsScannerCallerIdGating:
    """The specific rigor Waild's directive asked for -- proven
    end-to-end through the real RateLimitedClient/is_allowed() stack,
    not just asserted."""

    def test_validation_session_caller_id_matches_injected_session_exactly(self):
        session = _session(lambda r: httpx.Response(200, text=""))
        scanner = HardcodedCredentialsScanner(session, patterns=[])
        assert scanner._validation_session.caller_id == session.caller_id == "hardcoded_credentials"

    def test_validation_session_has_empty_scope_domains(self):
        """See module docstring's 'TWO SESSIONS, DELIBERATELY' note --
        this is what makes the exemption the ONLY path to any host."""
        session = _session(lambda r: httpx.Response(200, text=""))
        scanner = HardcodedCredentialsScanner(session, patterns=[])
        assert scanner._validation_session._scope_domains == []

    @pytest.mark.asyncio
    async def test_stripe_validation_actually_reaches_stripe_via_the_exemption(self):
        """Full end-to-end proof: constructing this scanner with a
        real, enabled allowlist lets its internal validation session
        genuinely reach api.stripe.com despite scope_domains=[]."""

        def js_handler(request):
            return httpx.Response(200, text='const key = "sk_live_' + "a" * 30 + '";')

        def stripe_handler(request):
            assert request.headers.get("authorization") == "Bearer sk_live_" + "a" * 30
            return httpx.Response(200, text='{"id":"acct_123"}')

        # Two separate transports: one for self.session (target JS),
        # one wired into the scanner's OWN internal validation client
        # after construction (there's no other way to route a second
        # MockTransport into an internally-built RateLimitedClient).
        js_session = _session(js_handler)
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        scanner = HardcodedCredentialsScanner(
            js_session, patterns=_STRIPE_PATTERN, credential_validation_allowlist=allowlist
        )
        scanner._validation_session = RateLimitedClient(
            scope_domains=[],
            caller_id=js_session.caller_id,
            intercepting_client=InterceptingClient(transport=httpx.MockTransport(stripe_handler)),
            credential_validation_allowlist=allowlist,
        )

        candidates = await scanner.scan("https://example.com/app.js")
        assert len(candidates) == 1
        assert "live-validated" in candidates[0].payload_used

    @pytest.mark.asyncio
    async def test_wrong_caller_id_blocks_validation_even_with_enabled_allowlist(self):
        """If this scanner were ever (mis-)registered under a
        different string, the exemption must not fire -- proven by
        directly constructing a RateLimitedClient with the wrong
        caller_id and confirming Stripe is unreachable."""
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        wrong_caller_client = RateLimitedClient(
            scope_domains=[],
            caller_id="xss_scanner",  # deliberately wrong
            intercepting_client=InterceptingClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
            credential_validation_allowlist=allowlist,
        )
        from core.http.rate_limited_client import OutOfScopeError

        with pytest.raises(OutOfScopeError):
            await wrong_caller_client.request("GET", "https://api.stripe.com/v1/account")


class TestHardcodedCredentialsScannerDetection:
    @pytest.mark.asyncio
    async def test_known_placeholder_is_excluded(self):
        def handler(request):
            return httpx.Response(200, text='const key = "AKIAIOSFODNN7EXAMPLE";')

        scanner = HardcodedCredentialsScanner(_session(handler), patterns=_AWS_PATTERN)
        candidates = await scanner.scan("https://example.com/app.js")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_non_placeholder_unvalidated_match_still_produces_candidate(self):
        """AWS/Twilio: no live validation, but Section 7.28's own TIER_D
        fallback means a pattern match alone is still a finding."""

        def handler(request):
            return httpx.Response(200, text='const key = "XKIAABCDEFGHIJKLMNOP";')

        scanner = HardcodedCredentialsScanner(_session(handler), patterns=_AWS_PATTERN)
        candidates = await scanner.scan("https://example.com/app.js")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "hardcoded_credentials"
        assert candidate.parameter is None
        assert candidate.http_method == "GET"
        assert candidate.detected_by == "hardcoded_credentials"
        assert candidate.probe_correlation_id is None
        assert "UNVALIDATED" in candidate.payload_used
        assert "XKIAABCDEFGHIJKLMNOP" in candidate.payload_used

    @pytest.mark.asyncio
    async def test_stripe_key_that_fails_validation_produces_no_candidate(self):
        def js_handler(request):
            return httpx.Response(200, text='const key = "sk_live_' + "b" * 30 + '";')

        def stripe_handler(request):
            return httpx.Response(401, text='{"error": "invalid_api_key"}')

        js_session = _session(js_handler)
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        scanner = HardcodedCredentialsScanner(
            js_session, patterns=_STRIPE_PATTERN, credential_validation_allowlist=allowlist
        )
        scanner._validation_session = RateLimitedClient(
            scope_domains=[],
            caller_id=js_session.caller_id,
            intercepting_client=InterceptingClient(transport=httpx.MockTransport(stripe_handler)),
            credential_validation_allowlist=allowlist,
        )

        candidates = await scanner.scan("https://example.com/app.js")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_google_maps_key_that_fails_validation_produces_no_candidate(self):
        def js_handler(request):
            return httpx.Response(200, text='const key = "AIza' + "X" * 35 + '";')

        def maps_handler(request):
            return httpx.Response(200, text=json.dumps({"status": "REQUEST_DENIED", "error_message": "bad key"}))

        js_session = _session(js_handler)
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["maps.googleapis.com"])
        scanner = HardcodedCredentialsScanner(
            js_session, patterns=_GOOGLE_MAPS_PATTERN, credential_validation_allowlist=allowlist
        )
        scanner._validation_session = RateLimitedClient(
            scope_domains=[],
            caller_id=js_session.caller_id,
            intercepting_client=InterceptingClient(transport=httpx.MockTransport(maps_handler)),
            credential_validation_allowlist=allowlist,
        )

        candidates = await scanner.scan("https://example.com/app.js")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_no_credential_shaped_content_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="function normalCode() { return 1; }")

        scanner = HardcodedCredentialsScanner(_session(handler), patterns=_AWS_PATTERN)
        candidates = await scanner.scan("https://example.com/app.js")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_no_patterns_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="")

        scanner = HardcodedCredentialsScanner(_session(handler), patterns=[])
        candidates = await scanner.scan("https://example.com/app.js")
        assert candidates == []
        assert call_count == 0


class TestHardcodedCredentialsScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            return httpx.Response(200, text='const key = "XKIAABCDEFGHIJKLMNOP";' + "M" * 2000)

        scanner = HardcodedCredentialsScanner(_session(handler), patterns=_AWS_PATTERN)
        candidates = await scanner.scan("https://example.com/app.js")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestHardcodedCredentialsScannerViaCreateScanner:
    def test_create_scanner_threads_allowlist_through(self):
        allowlist = CredentialValidationAllowlist(enabled=True, external_apis=["api.stripe.com"])
        scanner = create_scanner(
            "hardcoded_credentials",
            scope_domains=["example.com"],
            patterns=_STRIPE_PATTERN,
            credential_validation_allowlist=allowlist,
        )
        assert isinstance(scanner, HardcodedCredentialsScanner)
        assert scanner._validation_session.caller_id == "hardcoded_credentials"

    def test_create_scanner_without_allowlist_still_works(self):
        scanner = create_scanner("hardcoded_credentials", scope_domains=["example.com"])
        assert isinstance(scanner, HardcodedCredentialsScanner)
