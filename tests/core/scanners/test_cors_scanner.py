"""
Implements: test coverage for core/scanners/cors_scanner.py (Section 7.11).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import inspect

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.cors_scanner import CORSScanner
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="cors_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


class TestCORSScannerRegistration:
    def test_registered_under_cors_scanner(self):
        assert SCANNER_REGISTRY["cors_scanner"] is CORSScanner

    def test_no_payloads_parameter(self):
        """See module docstring -- no payload file by design."""
        sig = inspect.signature(CORSScanner.__init__)
        assert "payloads" not in sig.parameters

    def test_no_interactsh_client_parameter(self):
        sig = inspect.signature(CORSScanner.__init__)
        assert "interactsh_client" not in sig.parameters


class TestCORSScannerOriginReflection:
    @pytest.mark.asyncio
    async def test_reflected_origin_produces_one_candidate(self):
        def handler(request):
            if request.method == "OPTIONS":
                return httpx.Response(204)
            origin = request.headers.get("origin")
            if origin and origin.startswith("https://xbow-cors-"):
                return httpx.Response(200, headers={"Access-Control-Allow-Origin": origin}, text="ok")
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")

        signal1 = [c for c in candidates if c.http_method == "GET" and c.payload_used.startswith("Origin: https://xbow-cors-")]
        assert len(signal1) == 1
        candidate = signal1[0]
        assert candidate.vuln_type == "cors"
        assert candidate.parameter is None
        assert candidate.detected_by == "cors_scanner"
        assert candidate.probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_non_reflected_origin_produces_no_reflection_candidate(self):
        def handler(request):
            return httpx.Response(200, headers={"Access-Control-Allow-Origin": "https://legit.example.com"}, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        reflection_candidates = [c for c in candidates if c.payload_used.startswith("Origin: https://xbow-cors-")]
        assert reflection_candidates == []

    @pytest.mark.asyncio
    async def test_separate_scan_calls_use_distinct_random_origins(self):
        """Within ONE scan() call, the same probe_origin is correctly
        reused across the GET and OPTIONS requests (signals 1/2/3 all
        test the same injected origin) -- only ACROSS separate scan()
        calls should the origin differ."""
        seen_origins_per_scan = []

        def handler(request):
            origin = request.headers.get("origin")
            if origin and origin.startswith("https://xbow-cors-") and request.method == "GET":
                seen_origins_per_scan.append(origin)
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        await scanner.scan("https://example.com/api/data")
        await scanner.scan("https://example.com/api/data")
        assert len(seen_origins_per_scan) == 2
        assert seen_origins_per_scan[0] != seen_origins_per_scan[1]


class TestCORSScannerCredentials:
    @pytest.mark.asyncio
    async def test_reflection_plus_acac_produces_two_candidates(self):
        def handler(request):
            if request.method == "OPTIONS":
                return httpx.Response(204)
            origin = request.headers.get("origin")
            if origin and origin.startswith("https://xbow-cors-"):
                return httpx.Response(
                    200,
                    headers={"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"},
                    text="ok",
                )
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        reflection_candidates = [c for c in candidates if c.payload_used.startswith("Origin: https://xbow-cors-") and c.http_method == "GET"]
        assert len(reflection_candidates) == 2  # signal 1 AND signal 2

    @pytest.mark.asyncio
    async def test_reflection_without_acac_produces_only_one_candidate(self):
        def handler(request):
            if request.method == "OPTIONS":
                return httpx.Response(204)
            origin = request.headers.get("origin")
            if origin and origin.startswith("https://xbow-cors-"):
                return httpx.Response(200, headers={"Access-Control-Allow-Origin": origin}, text="ok")
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        reflection_candidates = [c for c in candidates if c.payload_used.startswith("Origin: https://xbow-cors-") and c.http_method == "GET"]
        assert len(reflection_candidates) == 1  # signal 1 only, no ACAC


class TestCORSScannerPreflightBypass:
    @pytest.mark.asyncio
    async def test_preflight_granting_put_produces_candidate(self):
        def handler(request):
            if request.method == "OPTIONS":
                origin = request.headers.get("origin")
                return httpx.Response(
                    204,
                    headers={"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, POST, PUT"},
                )
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        preflight_candidates = [c for c in candidates if c.http_method == "OPTIONS"]
        assert len(preflight_candidates) == 1

    @pytest.mark.asyncio
    async def test_preflight_not_granting_put_produces_no_candidate(self):
        def handler(request):
            if request.method == "OPTIONS":
                return httpx.Response(204, headers={"Access-Control-Allow-Methods": "GET"})
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        preflight_candidates = [c for c in candidates if c.http_method == "OPTIONS"]
        assert preflight_candidates == []


class TestCORSScannerNullOrigin:
    @pytest.mark.asyncio
    async def test_null_origin_accepted_produces_candidate(self):
        def handler(request):
            if request.method == "OPTIONS":
                return httpx.Response(204)
            origin = request.headers.get("origin")
            if origin == "null":
                return httpx.Response(200, headers={"Access-Control-Allow-Origin": "null"}, text="ok")
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        null_candidates = [c for c in candidates if c.payload_used == "Origin: null"]
        assert len(null_candidates) == 1

    @pytest.mark.asyncio
    async def test_null_origin_rejected_produces_no_candidate(self):
        def handler(request):
            if request.method == "OPTIONS":
                return httpx.Response(204)
            return httpx.Response(200, text="ok")  # no ACAO at all

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        null_candidates = [c for c in candidates if c.payload_used == "Origin: null"]
        assert null_candidates == []


class TestCORSScannerCleanTarget:
    @pytest.mark.asyncio
    async def test_no_cors_headers_at_all_produces_zero_candidates(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        assert candidates == []


class TestCORSScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            if request.method == "OPTIONS":
                return httpx.Response(204)
            origin = request.headers.get("origin")
            if origin and origin.startswith("https://xbow-cors-"):
                return httpx.Response(200, headers={"Access-Control-Allow-Origin": origin}, text="I" * 2000)
            return httpx.Response(200, text="ok")

        scanner = CORSScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/api/data")
        reflection_candidates = [c for c in candidates if c.http_method == "GET" and c.payload_used.startswith("Origin: https://xbow-cors-")]
        assert len(reflection_candidates[0].raw_response_snapshot) == 512


class TestCORSScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("cors_scanner", scope_domains=["example.com"])
        assert isinstance(scanner, CORSScanner)
