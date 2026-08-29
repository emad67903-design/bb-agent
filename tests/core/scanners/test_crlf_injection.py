"""
Implements: test coverage for core/scanners/crlf_injection.py (Section 7.17).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.crlf_injection import CRLFInjectionScanner, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="crlf_injection",
        intercepting_client=InterceptingClient(transport=transport),
    )


_ONE_PAYLOAD = [
    {"id": "single_header_injection", "payload": "\r\nX-XBOW-PROBE: 1", "probe_header_name": "X-XBOW-PROBE", "probe_header_value": "1"}
]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_correct_encoding(self):
        payloads = _load_payloads()
        assert len(payloads) == 1
        assert payloads[0]["payload"] == "\r\nX-XBOW-PROBE: 1"
        assert payloads[0]["probe_header_name"] == "X-XBOW-PROBE"
        assert payloads[0]["probe_header_value"] == "1"


class TestCRLFInjectionScannerRegistration:
    def test_registered_under_crlf_injection(self):
        assert SCANNER_REGISTRY["crlf_injection"] is CRLFInjectionScanner

    def test_no_interactsh_client_parameter(self):
        """See module docstring -- this scanner has no OOB path."""
        import inspect

        sig = inspect.signature(CRLFInjectionScanner.__init__)
        assert "interactsh_client" not in sig.parameters


class TestCRLFInjectionScannerNoQueryParams:
    @pytest.mark.asyncio
    async def test_no_query_params_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = CRLFInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/no-params")
        assert candidates == []
        assert call_count == 0


class TestCRLFInjectionScannerDetection:
    @pytest.mark.asyncio
    async def test_reflected_header_produces_candidate(self):
        def handler(request):
            # Simulate a vulnerable target: the injected CRLF sequence
            # made it into the raw response headers.
            query = request.url.query.decode()
            if "%0D%0AX-XBOW-PROBE" in query.upper():
                return httpx.Response(200, headers={"X-XBOW-PROBE": "1"}, text="ok")
            return httpx.Response(200, text="ok")

        scanner = CRLFInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/redirect?url=x")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "crlf_injection"
        assert candidate.parameter == "url"
        assert candidate.detected_by == "crlf_injection"
        assert candidate.http_method == "GET"
        assert candidate.probe_correlation_id is None
        assert candidate.payload_used == "\r\nX-XBOW-PROBE: 1"

    @pytest.mark.asyncio
    async def test_header_reflected_in_body_only_is_not_a_signal(self):
        """See module docstring's 'DETECTION IS HEADER-BASED, NOT
        BODY-BASED' note -- a body-text match alone must not fire."""

        def handler(request):
            return httpx.Response(200, text="Your request included: X-XBOW-PROBE: 1 in the URL")

        scanner = CRLFInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/redirect?url=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_no_reflection_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="normal page")

        scanner = CRLFInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/redirect?url=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_wrong_header_value_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, headers={"X-XBOW-PROBE": "not-1"}, text="ok")

        scanner = CRLFInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/redirect?url=x")
        assert candidates == []


class TestCRLFInjectionScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            return httpx.Response(200, headers={"X-XBOW-PROBE": "1"}, text="F" * 2000)

        scanner = CRLFInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/redirect?url=x")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestCRLFInjectionScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("crlf_injection", scope_domains=["example.com"])
        assert isinstance(scanner, CRLFInjectionScanner)
