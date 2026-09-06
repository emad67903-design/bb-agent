"""
Implements: test coverage for core/scanners/oauth_scanner.py (Section 7.14).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import inspect

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.oauth_scanner import (
    OAuthScanner,
    _has_oauth_response_params,
    _load_payloads,
    _remove_query_param,
)
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="oauth_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


_PLAIN_ONLY = [{"id": "plain_attacker_url", "technique": "redirect_uri_variant", "payload_template": "https://xbow-oauth-probe.example"}]


class TestHasOauthResponseParams:
    def test_code_in_query_matches(self):
        assert _has_oauth_response_params("https://x.example/?code=abc123&state=xyz") is True

    def test_access_token_in_fragment_matches(self):
        assert _has_oauth_response_params("https://x.example/#access_token=abc123&token_type=Bearer") is True

    def test_bare_redirect_no_match(self):
        assert _has_oauth_response_params("https://x.example/") is False

    def test_substring_collision_does_not_match(self):
        """statuscode contains 'code' as a substring but is not the
        real key 'code' -- must not false-positive."""
        assert _has_oauth_response_params("https://x.example/?statuscode=200") is False


class TestRemoveQueryParam:
    def test_removes_only_the_named_param(self):
        result = _remove_query_param("https://x.com/auth?client_id=1&state=abc&redirect_uri=y", "state")
        assert "state=" not in result
        assert "client_id=1" in result
        assert "redirect_uri=y" in result

    def test_no_such_param_is_a_no_op(self):
        result = _remove_query_param("https://x.com/auth?client_id=1", "state")
        assert result == "https://x.com/auth?client_id=1"


class TestLoadPayloads:
    def test_real_payload_file_has_three_variants(self):
        payloads = _load_payloads()
        assert {p["id"] for p in payloads} == {"plain_attacker_url", "at_sign_trick", "subdomain_suffix_trick"}

    def test_at_sign_and_subdomain_variants_use_target_host(self):
        payloads = _load_payloads()
        at_sign = next(p for p in payloads if p["id"] == "at_sign_trick")
        subdomain = next(p for p in payloads if p["id"] == "subdomain_suffix_trick")
        assert "{target_host}" in at_sign["payload_template"]
        assert "{target_host}" in subdomain["payload_template"]


class TestOAuthScannerRegistration:
    def test_registered_under_oauth_scanner(self):
        assert SCANNER_REGISTRY["oauth_scanner"] is OAuthScanner

    def test_no_interactsh_client_parameter(self):
        sig = inspect.signature(OAuthScanner.__init__)
        assert "interactsh_client" not in sig.parameters


class TestOAuthScannerRedirectUri:
    @pytest.mark.asyncio
    async def test_no_redirect_uri_param_produces_no_candidates(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_accepted_redirect_uri_without_token_produces_one_candidate(self):
        def handler(request):
            query = request.url.query.decode()
            if "xbow-oauth-probe" in query:
                return httpx.Response(302, headers={"Location": "https://xbow-oauth-probe.example"}, text="")
            return httpx.Response(200, text="ok")

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1&redirect_uri=https://example.com/cb")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "oauth"
        assert candidate.parameter == "redirect_uri"
        assert candidate.http_method == "GET"
        assert candidate.detected_by == "oauth_scanner"
        assert candidate.probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_accepted_redirect_uri_with_code_produces_two_candidates(self):
        """Token leakage is a STRICTLY STRONGER signal on top of the
        acceptance signal -- both fire."""

        def handler(request):
            query = request.url.query.decode()
            if "xbow-oauth-probe" in query:
                return httpx.Response(
                    302, headers={"Location": "https://xbow-oauth-probe.example?code=SECRET123"}, text=""
                )
            return httpx.Response(200, text="ok")

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1&redirect_uri=https://example.com/cb")
        assert len(candidates) == 2

    @pytest.mark.asyncio
    async def test_rejected_redirect_uri_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(400, text="invalid redirect_uri")

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1&redirect_uri=https://example.com/cb")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_at_sign_variant_uses_target_host(self):
        entry = [{"id": "at_sign_trick", "technique": "redirect_uri_variant", "payload_template": "https://{target_host}@xbow-oauth-probe.example"}]
        expected_payload = "https://example.com@xbow-oauth-probe.example"

        def handler(request):
            query = request.url.query.decode()
            if "xbow-oauth-probe" in query:
                return httpx.Response(302, headers={"Location": expected_payload}, text="")
            return httpx.Response(200, text="ok")

        scanner = OAuthScanner(_session(handler), payloads=entry)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1&redirect_uri=https://example.com/cb")
        assert len(candidates) == 1
        assert candidates[0].payload_used == expected_payload


class TestOAuthScannerStateAbsence:
    @pytest.mark.asyncio
    async def test_no_state_param_produces_no_state_candidates(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_state_not_enforced_produces_candidate(self):
        def handler(request):
            return httpx.Response(200, text="ok")  # succeeds whether state is present or not

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1&state=abc123")

        state_candidates = [c for c in candidates if c.parameter == "state"]
        assert len(state_candidates) == 1
        assert state_candidates[0].payload_used == "(removed)"

    @pytest.mark.asyncio
    async def test_state_enforced_produces_no_candidate(self):
        def handler(request):
            query = request.url.query.decode()
            if "state=" in query:
                return httpx.Response(200, text="ok")
            return httpx.Response(400, text="missing state parameter")

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1&state=abc123")
        state_candidates = [c for c in candidates if c.parameter == "state"]
        assert state_candidates == []


class TestOAuthScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            query = request.url.query.decode()
            if "xbow-oauth-probe" in query:
                return httpx.Response(
                    302, headers={"Location": "https://xbow-oauth-probe.example"}, text="P" * 2000
                )
            return httpx.Response(200, text="ok")

        scanner = OAuthScanner(_session(handler), payloads=_PLAIN_ONLY)
        candidates = await scanner.scan("https://example.com/authorize?client_id=1&redirect_uri=https://example.com/cb")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestOAuthScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("oauth_scanner", scope_domains=["example.com"])
        assert isinstance(scanner, OAuthScanner)
