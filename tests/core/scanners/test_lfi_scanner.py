"""
Implements: test coverage for core/scanners/lfi_scanner.py (Section 7.10).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.lfi_scanner import LFIScanner, _is_windows_fingerprinted, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="lfi_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _path(request: httpx.Request) -> str:
    return urllib.parse.unquote(request.url.query.decode())


_HOSTNAME_ONLY = [{"id": "hostname_raw", "encoding": "raw", "payload": "../../../etc/hostname"}]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_three_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 3
        assert {p["encoding"] for p in payloads} == {"raw", "url_encoded", "double_url_encoded"}


class TestIsWindowsFingerprinted:
    def test_iis_server_header_detected(self):
        assert _is_windows_fingerprinted(httpx.Headers({"Server": "Microsoft-IIS/10.0"})) is True

    def test_asp_net_powered_by_header_detected(self):
        assert _is_windows_fingerprinted(httpx.Headers({"X-Powered-By": "ASP.NET"})) is True

    def test_x_aspnet_version_presence_alone_detected(self):
        assert _is_windows_fingerprinted(httpx.Headers({"X-AspNet-Version": "4.0.30319"})) is True

    def test_apache_server_header_not_detected(self):
        assert _is_windows_fingerprinted(httpx.Headers({"Server": "Apache/2.4.41"})) is False

    def test_no_relevant_headers_not_detected(self):
        assert _is_windows_fingerprinted(httpx.Headers({"Content-Type": "text/html"})) is False


class TestLFIScannerRegistration:
    def test_registered_under_lfi_scanner(self):
        assert SCANNER_REGISTRY["lfi_scanner"] is LFIScanner


class TestLFIScannerWindowsDeferral:
    @pytest.mark.asyncio
    async def test_windows_fingerprint_returns_empty_and_stops_after_baseline(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, headers={"Server": "Microsoft-IIS/10.0"}, text="Windows page")

        scanner = LFIScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        candidates = await scanner.scan("https://example.com/file?name=readme.txt")
        assert candidates == []
        assert call_count == 1  # only the baseline -- no LFI payloads attempted

    @pytest.mark.asyncio
    async def test_windows_fingerprint_logs_the_deferral_marker(self, caplog):
        def handler(request):
            return httpx.Response(200, headers={"Server": "Microsoft-IIS/10.0"}, text="Windows page")

        import logging

        with caplog.at_level(logging.INFO):
            scanner = LFIScanner(_session(handler), payloads=_HOSTNAME_ONLY)
            await scanner.scan("https://example.com/file?name=readme.txt")
        assert "[LFI_WINDOWS_DEFERRED]" in caplog.text


class TestLFIScannerLinuxDetection:
    @pytest.mark.asyncio
    async def test_hostname_like_content_produces_candidate(self):
        def handler(request):
            if "etc" in _path(request) and "hostname" in _path(request):
                return httpx.Response(200, headers={"Server": "nginx"}, text="web-server-01")
            return httpx.Response(200, headers={"Server": "nginx"}, text="<html>normal page</html>")

        scanner = LFIScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        candidates = await scanner.scan("https://example.com/file?name=readme.txt")
        assert len(candidates) == 1
        c = candidates[0]
        assert c.vuln_type == "lfi"
        assert c.parameter == "name"
        assert c.detected_by == "lfi_scanner"
        assert c.payload_used == "../../../etc/hostname"

    @pytest.mark.asyncio
    async def test_non_hostname_content_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, headers={"Server": "nginx"}, text="<html>file not found</html>")

        scanner = LFIScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        candidates = await scanner.scan("https://example.com/file?name=readme.txt")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_all_three_real_encodings_tested_independently(self):
        def handler(request):
            if "hostname" in _path(request):
                return httpx.Response(200, headers={"Server": "nginx"}, text="web-server-01")
            return httpx.Response(200, headers={"Server": "nginx"}, text="<html>baseline</html>")

        scanner = LFIScanner(_session(handler))  # real payload file: 3 encodings
        candidates = await scanner.scan("https://example.com/file?name=readme.txt")
        assert len(candidates) == 3
        assert {c.payload_used for c in candidates} == {
            "../../../etc/hostname",
            "..%2f..%2f..%2fetc%2fhostname",
            "..%252f..%252f..%252fetc%252fhostname",
        }

    @pytest.mark.asyncio
    async def test_baseline_fetched_exactly_once(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, headers={"Server": "nginx"}, text="baseline")

        scanner = LFIScanner(_session(handler))  # 3 payloads
        await scanner.scan("https://example.com/file?name=readme.txt&lang=en")
        # 1 baseline + (3 payloads x 2 params) = 7
        assert call_count == 7

    @pytest.mark.asyncio
    async def test_no_query_params_makes_no_requests_at_all(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, headers={"Server": "nginx"}, text="ok")

        scanner = LFIScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        candidates = await scanner.scan("https://example.com/file")
        assert candidates == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_out_of_scope_url_raises(self):
        from core.http.rate_limited_client import OutOfScopeError

        def handler(request):
            return httpx.Response(200, headers={"Server": "nginx"}, text="ok")

        scanner = LFIScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        with pytest.raises(OutOfScopeError):
            await scanner.scan("https://evil.com/file?name=readme.txt")

    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            if "hostname" in _path(request):
                return httpx.Response(200, headers={"Server": "nginx"}, text="web-server-01")
            return httpx.Response(200, headers={"Server": "nginx"}, text="x" * 1000)

        scanner = LFIScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        candidates = await scanner.scan("https://example.com/file?name=readme.txt")
        assert len(candidates[0].raw_response_snapshot) <= 512
