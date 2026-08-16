"""
Implements: test coverage for core/scanners/ssti_scanner.py (Section 7.7).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.registry import SCANNER_REGISTRY
from core.scanners.ssti_scanner import SSTIScanner, _load_payloads


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="ssti_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _query(request: httpx.Request) -> str:
    return urllib.parse.unquote(request.url.query.decode())


_JINJA_ONLY = [{"id": "jinja2_multiply", "engine": "jinja2", "payload": "{{7*7}}", "expected": "49"}]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_five_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 5
        assert {p["engine"] for p in payloads} == {"jinja2", "twig", "freemarker", "smarty", "velocity"}

    def test_jinja2_and_twig_match_section_7_7_exactly(self):
        """The only two directly blueprint-cited entries."""
        payloads = {p["engine"]: p for p in _load_payloads()}
        assert payloads["jinja2"]["payload"] == "{{7*7}}"
        assert payloads["jinja2"]["expected"] == "49"
        assert payloads["twig"]["payload"] == "{7*7}"
        assert payloads["twig"]["expected"] == "49"


class TestSSTIScannerRegistration:
    def test_registered_under_ssti_scanner(self):
        assert SCANNER_REGISTRY["ssti_scanner"] is SSTIScanner


class TestSSTIScanner:
    @pytest.mark.asyncio
    async def test_expected_value_appearing_only_in_payload_response_produces_candidate(self):
        def handler(request):
            q = _query(request)
            body = "49" if "7*7" in q else "no result"
            return httpx.Response(200, text=body)

        scanner = SSTIScanner(_session(handler), payloads=_JINJA_ONLY)
        candidates = await scanner.scan("https://example.com/greet?name=x")
        assert len(candidates) == 1
        c = candidates[0]
        assert c.vuln_type == "ssti"
        assert c.parameter == "name"
        assert c.payload_used == "{{7*7}}"
        assert c.detected_by == "ssti_scanner"

    @pytest.mark.asyncio
    async def test_expected_value_present_in_baseline_too_produces_no_candidate(self):
        """False-positive guard: "49" already on the unmodified page
        (e.g. a price) must not be mistaken for template evaluation."""
        def handler(request):
            return httpx.Response(200, text="Price: $49.00")  # every response, baseline included

        scanner = SSTIScanner(_session(handler), payloads=_JINJA_ONLY)
        candidates = await scanner.scan("https://example.com/greet?name=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_expected_value_never_appears_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="hello, x")

        scanner = SSTIScanner(_session(handler), payloads=_JINJA_ONLY)
        candidates = await scanner.scan("https://example.com/greet?name=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_baseline_fetched_exactly_once_regardless_of_param_or_payload_count(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="no match here")

        scanner = SSTIScanner(_session(handler))  # real payload file: 5 payloads
        await scanner.scan("https://example.com/greet?name=x&title=y")
        # 1 baseline + (5 payloads x 2 params) = 11
        assert call_count == 11

    @pytest.mark.asyncio
    async def test_no_query_params_makes_no_requests_at_all(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, text="ok")

        scanner = SSTIScanner(_session(handler), payloads=_JINJA_ONLY)
        candidates = await scanner.scan("https://example.com/greet")
        assert candidates == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_multiple_engines_each_tested_independently(self):
        def handler(request):
            q = _query(request)
            if "7*7" in q or "x=7*7" in q:
                return httpx.Response(200, text="49")
            return httpx.Response(200, text="no result")

        scanner = SSTIScanner(_session(handler))  # all 5 real payloads
        candidates = await scanner.scan("https://example.com/greet?name=x")
        assert len(candidates) == 5

    @pytest.mark.asyncio
    async def test_out_of_scope_url_raises(self):
        from core.http.rate_limited_client import OutOfScopeError

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = SSTIScanner(_session(handler), payloads=_JINJA_ONLY)
        with pytest.raises(OutOfScopeError):
            await scanner.scan("https://evil.com/greet?name=x")

    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            q = _query(request)
            text = "49" + "x" * 1000 if "7*7" in q else "no match" + "x" * 1000
            return httpx.Response(200, text=text)

        scanner = SSTIScanner(_session(handler), payloads=_JINJA_ONLY)
        candidates = await scanner.scan("https://example.com/greet?name=x")
        assert len(candidates[0].raw_response_snapshot) == 512
