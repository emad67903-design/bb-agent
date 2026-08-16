"""
Implements: test coverage for core/scanners/xss_scanner.py (Section 7.1).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.registry import SCANNER_REGISTRY
from core.scanners.xss_scanner import XSSScanner, _load_payloads, _make_marker

_TEST_PAYLOADS = [
    {"id": "body", "context": "html_body", "payload_template": "<img src=x onerror=console.log('{marker}')>"},
    {"id": "attr", "context": "html_attribute", "payload_template": "\"><b>{marker}</b>"},
]


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="xss_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _reflecting_handler(request: httpx.Request) -> httpx.Response:
    """Echoes back the raw query string in the body -- simulates an app
    that reflects input verbatim (vulnerable)."""
    query = urllib.parse.unquote(str(request.url.query, "utf-8") if isinstance(request.url.query, bytes) else request.url.query)
    return httpx.Response(200, text=f"<html><body>Results for: {query}</body></html>")


def _escaping_handler(request: httpx.Request) -> httpx.Response:
    """Simulates an app that HTML-escapes input (not vulnerable)."""
    import html

    query = urllib.parse.unquote(str(request.url.query, "utf-8") if isinstance(request.url.query, bytes) else request.url.query)
    return httpx.Response(200, text=f"<html><body>Results for: {html.escape(query)}</body></html>")


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_six_entries(self):
        """Confirms the Week 7 real content (docs/DECISIONS.md item 75)
        actually loads -- not the Week 0 stub's empty list."""
        payloads = _load_payloads()
        assert len(payloads) == 6
        assert all({"id", "context", "payload_template"} <= p.keys() for p in payloads)


class TestMakeMarker:
    def test_markers_are_unique(self):
        assert _make_marker() != _make_marker()

    def test_marker_has_xbow_xss_prefix(self):
        assert _make_marker().startswith("XBOW_XSS_")


class TestXSSScannerRegistration:
    def test_registered_under_xss_scanner(self):
        assert SCANNER_REGISTRY["xss_scanner"] is XSSScanner


class TestXSSScanner:
    @pytest.mark.asyncio
    async def test_reflected_payload_produces_a_candidate(self):
        scanner = XSSScanner(_session(_reflecting_handler), payloads=_TEST_PAYLOADS)
        candidates = await scanner.scan("https://example.com/search?q=hello")
        assert len(candidates) == 2  # both payloads reflect verbatim

    @pytest.mark.asyncio
    async def test_escaped_payload_produces_no_candidates(self):
        scanner = XSSScanner(_session(_escaping_handler), payloads=_TEST_PAYLOADS)
        candidates = await scanner.scan("https://example.com/search?q=hello")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_no_query_params_makes_no_requests_and_returns_empty(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, text="never reached")

        scanner = XSSScanner(_session(handler), payloads=_TEST_PAYLOADS)
        candidates = await scanner.scan("https://example.com/search")
        assert candidates == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_candidate_fields_are_correct(self):
        scanner = XSSScanner(_session(_reflecting_handler), payloads=[_TEST_PAYLOADS[0]])
        candidates = await scanner.scan("https://example.com/search?q=hello")
        assert len(candidates) == 1
        c = candidates[0]
        assert c.vuln_type == "xss"
        assert c.endpoint == "https://example.com/search?q=hello"
        assert c.http_method == "GET"
        assert c.parameter == "q"
        assert c.detected_by == "xss_scanner"
        assert "onerror=console.log(" in c.payload_used
        assert c.payload_used in c.raw_response_snapshot

    @pytest.mark.asyncio
    async def test_multiple_params_each_tested_independently(self):
        scanner = XSSScanner(_session(_reflecting_handler), payloads=[_TEST_PAYLOADS[0]])
        candidates = await scanner.scan("https://example.com/search?q=hello&page=2")
        assert len(candidates) == 2
        assert {c.parameter for c in candidates} == {"q", "page"}

    @pytest.mark.asyncio
    async def test_two_scans_use_different_markers(self):
        """No cross-request marker collision -- module docstring's whole
        reason for random, not literal, markers."""
        seen_queries = []

        def handler(request):
            seen_queries.append(str(request.url))
            return httpx.Response(200, text=str(request.url))

        scanner = XSSScanner(_session(handler), payloads=[_TEST_PAYLOADS[0]])
        await scanner.scan("https://example.com/search?q=hello")
        await scanner.scan("https://example.com/search?q=hello")
        assert seen_queries[0] != seen_queries[1]

    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            query = urllib.parse.unquote(request.url.query.decode())
            return httpx.Response(200, text=f"{query}{'x' * 1000}")

        scanner = XSSScanner(_session(handler), payloads=[_TEST_PAYLOADS[0]])
        candidates = await scanner.scan("https://example.com/search?q=hello")
        assert len(candidates[0].raw_response_snapshot) == 512

    @pytest.mark.asyncio
    async def test_out_of_scope_url_raises_not_silently_skipped(self):
        """Scope enforcement is RateLimitedClient's job (Section 10.2),
        not this scanner's to catch or suppress -- confirms the scanner
        doesn't swallow OutOfScopeError."""
        from core.http.rate_limited_client import OutOfScopeError

        scanner = XSSScanner(_session(_reflecting_handler), payloads=[_TEST_PAYLOADS[0]])
        with pytest.raises(OutOfScopeError):
            await scanner.scan("https://evil.com/search?q=hello")

    @pytest.mark.asyncio
    async def test_uses_real_payload_file_when_not_overridden(self):
        """Production path (no `payloads=` override) -- confirms the
        constructor default actually wires to `_load_payloads()`."""
        scanner = XSSScanner(_session(_reflecting_handler))
        candidates = await scanner.scan("https://example.com/search?q=hello")
        assert len(candidates) == 6  # all 6 real payloads reflect verbatim
