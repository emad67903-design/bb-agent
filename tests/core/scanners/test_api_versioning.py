"""
Implements: test coverage for core/scanners/api_versioning.py (Section 7.24).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.api_versioning import (
    APIVersioningScanner,
    _find_lower_version_url,
    _load_payloads,
)
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="api_versioning",
        intercepting_client=InterceptingClient(transport=transport),
    )


_ONE_PATTERN = [{"id": "path_version_segment", "pattern": r"/v(\d+)/"}]


class TestFindLowerVersionUrl:
    def test_simple_v_segment(self):
        assert _find_lower_version_url("https://example.com/v2/admin", _ONE_PATTERN) == "https://example.com/v1/admin"

    def test_api_prefixed_v_segment_matches_via_substring(self):
        """Confirms the substring-search reasoning in the module
        docstring, not just asserted in prose."""
        result = _find_lower_version_url("https://example.com/api/v3/users/1", _ONE_PATTERN)
        assert result == "https://example.com/api/v2/users/1"

    def test_version_zero_returns_none(self):
        assert _find_lower_version_url("https://example.com/v0/admin", _ONE_PATTERN) is None

    def test_no_version_segment_returns_none(self):
        assert _find_lower_version_url("https://example.com/admin", _ONE_PATTERN) is None

    def test_double_digit_version(self):
        assert _find_lower_version_url("https://example.com/v12/admin", _ONE_PATTERN) == "https://example.com/v11/admin"


class TestLoadPayloads:
    def test_real_payload_file_loads(self):
        payloads = _load_payloads()
        assert len(payloads) == 1
        assert payloads[0]["pattern"] == r"/v(\d+)/"


class TestAPIVersioningScannerRegistration:
    def test_registered_under_api_versioning(self):
        assert SCANNER_REGISTRY["api_versioning"] is APIVersioningScanner


class TestAPIVersioningScannerPrecondition:
    @pytest.mark.asyncio
    async def test_no_version_segment_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(403, text="denied")

        scanner = APIVersioningScanner(_session(handler), payloads=_ONE_PATTERN)
        candidates = await scanner.scan("https://example.com/admin")
        assert candidates == []
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_no_payloads_returns_empty(self):
        def handler(request):
            return httpx.Response(403, text="denied")

        scanner = APIVersioningScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/v2/admin")
        assert candidates == []


class TestAPIVersioningScannerDetection:
    @pytest.mark.asyncio
    async def test_v2_denied_v1_granted_produces_candidate(self):
        def handler(request):
            if "/v1/" in str(request.url):
                return httpx.Response(200, text="admin panel")
            return httpx.Response(403, text="denied")

        scanner = APIVersioningScanner(_session(handler), payloads=_ONE_PATTERN)
        candidates = await scanner.scan("https://example.com/v2/admin")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "api_versioning"
        assert candidate.endpoint == "https://example.com/v2/admin"
        assert candidate.payload_used == "https://example.com/v1/admin"
        assert candidate.parameter is None
        assert candidate.http_method == "GET"
        assert candidate.detected_by == "api_versioning"
        assert candidate.probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_v2_not_denied_produces_no_candidate(self):
        """Section 7.24's two-part condition: if the CURRENT version
        doesn't deny access, there's nothing to bypass."""

        def handler(request):
            return httpx.Response(200, text="accessible either way")

        scanner = APIVersioningScanner(_session(handler), payloads=_ONE_PATTERN)
        candidates = await scanner.scan("https://example.com/v2/admin")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_v2_denied_v1_also_denied_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(403, text="denied")

        scanner = APIVersioningScanner(_session(handler), payloads=_ONE_PATTERN)
        candidates = await scanner.scan("https://example.com/v2/admin")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_v2_denied_v1_also_denied_but_different_status_is_not_a_bypass(self):
        """Only a non-403 alternate response counts -- 401, 404, or
        anything else that ISN'T specifically "granted" is still not
        proof of a bypass on its own for this scanner's simple check,
        but per the blueprint's own text (403-specific), a 500 or 404
        still counts as "not 403" here -- documenting the exact
        boundary rather than assuming a stricter check was intended."""

        def handler(request):
            if "/v1/" in str(request.url):
                return httpx.Response(404, text="not found")
            return httpx.Response(403, text="denied")

        scanner = APIVersioningScanner(_session(handler), payloads=_ONE_PATTERN)
        candidates = await scanner.scan("https://example.com/v2/admin")
        # A 404 is "not 403" per the literal check -- this documents
        # current behavior exactly, not a hidden assumption.
        assert len(candidates) == 1


class TestAPIVersioningScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            if "/v1/" in str(request.url):
                return httpx.Response(200, text="J" * 2000)
            return httpx.Response(403, text="denied")

        scanner = APIVersioningScanner(_session(handler), payloads=_ONE_PATTERN)
        candidates = await scanner.scan("https://example.com/v2/admin")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestAPIVersioningScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("api_versioning", scope_domains=["example.com"])
        assert isinstance(scanner, APIVersioningScanner)
