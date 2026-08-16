"""
Implements: test coverage for core/scanners/path_traversal.py (Section 7.18).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.path_traversal import PathTraversalScanner, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="path_traversal",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _path(request: httpx.Request) -> str:
    return urllib.parse.unquote(request.url.query.decode())


_LINUX_ONLY = [{"id": "linux_hostname_raw", "target_os": "linux", "target_file": "hostname", "payload": "../../../etc/hostname"}]
_WINDOWS_ONLY = [{"id": "windows_win_ini", "target_os": "windows", "target_file": "win_ini", "payload": "..\\..\\..\\..\\windows\\win.ini"}]
_WIN_INI_BODY = "[fonts]\n[extensions]\nkey=val"


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_three_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 3
        assert {p["target_os"] for p in payloads} == {"linux", "windows"}

    def test_linux_and_windows_payloads_match_section_7_18_exactly(self):
        payloads = {p["id"]: p for p in _load_payloads()}
        assert payloads["linux_hostname_raw"]["payload"] == "../../../etc/hostname"
        assert payloads["windows_win_ini_backslash"]["payload"] == "..\\..\\..\\..\\windows\\win.ini"

    def test_zip_and_api_variants_are_deliberately_absent(self):
        """Section 7.18 names these categories but gives no concrete
        pattern for either -- docs/DECISIONS.md item 79 flags this as
        an open gap, not silently filled in. Pins the absence the same
        way test_base_scanner.py once pinned scan()'s deliberate
        absence, so a future addition is a conscious decision."""
        target_files = {p["target_file"] for p in _load_payloads()}
        assert target_files == {"hostname", "win_ini"}


class TestPathTraversalScannerRegistration:
    def test_registered_under_path_traversal(self):
        assert SCANNER_REGISTRY["path_traversal"] is PathTraversalScanner


class TestPathTraversalScannerLinux:
    @pytest.mark.asyncio
    async def test_hostname_like_content_produces_candidate(self):
        def handler(request):
            if "hostname" in _path(request):
                return httpx.Response(200, text="web-server-01")
            return httpx.Response(200, text="<html>baseline</html>")

        scanner = PathTraversalScanner(_session(handler), payloads=_LINUX_ONLY)
        candidates = await scanner.scan("https://example.com/download?file=readme.txt")
        assert len(candidates) == 1
        c = candidates[0]
        assert c.vuln_type == "path_traversal"
        assert c.detected_by == "path_traversal"
        assert c.parameter == "file"

    @pytest.mark.asyncio
    async def test_non_hostname_content_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="<html>not found</html>")

        scanner = PathTraversalScanner(_session(handler), payloads=_LINUX_ONLY)
        candidates = await scanner.scan("https://example.com/download?file=readme.txt")
        assert candidates == []


class TestPathTraversalScannerWindows:
    @pytest.mark.asyncio
    async def test_win_ini_content_produces_candidate(self):
        def handler(request):
            if "windows" in _path(request).lower():
                return httpx.Response(200, text=_WIN_INI_BODY)
            return httpx.Response(200, text="<html>baseline</html>")

        scanner = PathTraversalScanner(_session(handler), payloads=_WINDOWS_ONLY)
        candidates = await scanner.scan("https://example.com/download?file=readme.txt")
        assert len(candidates) == 1
        assert candidates[0].payload_used == "..\\..\\..\\..\\windows\\win.ini"

    @pytest.mark.asyncio
    async def test_non_win_ini_content_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="<html>not found</html>")

        scanner = PathTraversalScanner(_session(handler), payloads=_WINDOWS_ONLY)
        candidates = await scanner.scan("https://example.com/download?file=readme.txt")
        assert candidates == []


class TestPathTraversalScannerCombined:
    @pytest.mark.asyncio
    async def test_no_fingerprint_gating_both_os_payloads_always_tried(self):
        """Unlike lfi_scanner.py -- see module docstring. A target that
        looks like neither OS's file still gets both tested, no skip."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="<html>baseline</html>")

        scanner = PathTraversalScanner(_session(handler))  # real file: 3 payloads
        await scanner.scan("https://example.com/download?file=readme.txt")
        # 1 baseline + 3 payloads x 1 param = 4
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_all_three_real_payloads_can_each_independently_detect(self):
        def handler(request):
            q = _path(request).lower()
            if "hostname" in q:
                return httpx.Response(200, text="web-01")
            if "windows" in q:
                return httpx.Response(200, text=_WIN_INI_BODY)
            return httpx.Response(200, text="<html>baseline</html>")

        scanner = PathTraversalScanner(_session(handler))
        candidates = await scanner.scan("https://example.com/download?file=readme.txt")
        assert len(candidates) == 3  # linux hostname + both windows win.ini variants

    @pytest.mark.asyncio
    async def test_no_query_params_makes_no_requests_at_all(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, text="ok")

        scanner = PathTraversalScanner(_session(handler), payloads=_LINUX_ONLY)
        candidates = await scanner.scan("https://example.com/download")
        assert candidates == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_out_of_scope_url_raises(self):
        from core.http.rate_limited_client import OutOfScopeError

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = PathTraversalScanner(_session(handler), payloads=_LINUX_ONLY)
        with pytest.raises(OutOfScopeError):
            await scanner.scan("https://evil.com/download?file=readme.txt")

    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            if "hostname" in _path(request):
                return httpx.Response(200, text="web-01")
            return httpx.Response(200, text="x" * 1000)

        scanner = PathTraversalScanner(_session(handler), payloads=_LINUX_ONLY)
        candidates = await scanner.scan("https://example.com/download?file=readme.txt")
        assert len(candidates[0].raw_response_snapshot) <= 512
