"""
Implements: test coverage for core/scanners/open_redirect.py (Section 7.19).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.open_redirect import OpenRedirectScanner, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="open_redirect",
        intercepting_client=InterceptingClient(transport=transport),
    )


_ABSOLUTE_ONLY = [{"id": "absolute_url", "payload": "https://example.com"}]


class TestLoadPayloads:
    def test_real_payload_file_has_all_four_variants(self):
        payloads = _load_payloads()
        assert {p["id"] for p in payloads} == {"absolute_url", "protocol_relative", "javascript_uri", "data_uri"}

    def test_javascript_and_data_uris_are_non_executing(self):
        payloads = _load_payloads()
        js_entry = next(p for p in payloads if p["id"] == "javascript_uri")
        data_entry = next(p for p in payloads if p["id"] == "data_uri")
        assert js_entry["payload"] == "javascript:void(0)"
        assert data_entry["payload"].startswith("data:text/plain,")


class TestOpenRedirectScannerRegistration:
    def test_registered_under_open_redirect(self):
        assert SCANNER_REGISTRY["open_redirect"] is OpenRedirectScanner


class TestOpenRedirectScannerNoQueryParams:
    @pytest.mark.asyncio
    async def test_no_query_params_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = OpenRedirectScanner(_session(handler), payloads=_ABSOLUTE_ONLY)
        candidates = await scanner.scan("https://example.com/no-params")
        assert candidates == []


class TestOpenRedirectScannerDetection:
    @pytest.mark.asyncio
    async def test_exact_location_match_produces_candidate(self):
        def handler(request):
            query = request.url.query.decode()
            if "example.com" in query and "redirect=https" in query:
                return httpx.Response(302, headers={"Location": "https://example.com"}, text="")
            return httpx.Response(200, text="normal page")  # baseline

        scanner = OpenRedirectScanner(_session(handler), payloads=_ABSOLUTE_ONLY)
        candidates = await scanner.scan("https://example.com/go?redirect=home")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "open_redirect"
        assert candidate.parameter == "redirect"
        assert candidate.payload_used == "https://example.com"
        assert candidate.http_method == "GET"
        assert candidate.detected_by == "open_redirect"
        assert candidate.probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_non_redirect_status_produces_no_candidate(self):
        def handler(request):
            # Reflects the value in a body, not a real redirect -- must not count.
            return httpx.Response(200, text="Location would be: https://example.com")

        scanner = OpenRedirectScanner(_session(handler), payloads=_ABSOLUTE_ONLY)
        candidates = await scanner.scan("https://example.com/go?redirect=home")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_redirect_to_different_location_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(302, headers={"Location": "https://legit-internal-page.example.com"}, text="")

        scanner = OpenRedirectScanner(_session(handler), payloads=_ABSOLUTE_ONLY)
        candidates = await scanner.scan("https://example.com/go?redirect=home")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_baseline_already_redirecting_there_produces_no_candidate(self):
        """Differential requirement -- if baseline ALSO redirects to the
        same place (e.g. a generic catch-all redirect unrelated to the
        payload), this isn't a genuine finding."""

        def handler(request):
            return httpx.Response(302, headers={"Location": "https://example.com"}, text="")

        scanner = OpenRedirectScanner(_session(handler), payloads=_ABSOLUTE_ONLY)
        candidates = await scanner.scan("https://example.com/go?redirect=home")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_protocol_relative_variant_detected(self):
        entry = [{"id": "protocol_relative", "payload": "//xbow-open-redirect-probe.example"}]

        def handler(request):
            query = request.url.query.decode()
            if "xbow-open-redirect-probe" in query:
                return httpx.Response(302, headers={"Location": "//xbow-open-redirect-probe.example"}, text="")
            return httpx.Response(200, text="normal page")

        scanner = OpenRedirectScanner(_session(handler), payloads=entry)
        candidates = await scanner.scan("https://example.com/go?redirect=home")
        assert len(candidates) == 1
        assert candidates[0].payload_used == "//xbow-open-redirect-probe.example"


class TestOpenRedirectScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            query = request.url.query.decode()
            if "example.com" in query:
                return httpx.Response(302, headers={"Location": "https://example.com"}, text="G" * 2000)
            return httpx.Response(200, text="normal page")

        scanner = OpenRedirectScanner(_session(handler), payloads=_ABSOLUTE_ONLY)
        candidates = await scanner.scan("https://example.com/go?redirect=home")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestOpenRedirectScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("open_redirect", scope_domains=["example.com"])
        assert isinstance(scanner, OpenRedirectScanner)
