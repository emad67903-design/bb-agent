"""
Implements: test coverage for core/scanners/sqli_scanner.py (Section 7.2).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.registry import SCANNER_REGISTRY
from core.scanners.sqli_scanner import DB_ERROR_SIGNATURES, SQLiScanner, _load_payloads, _make_marker


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="sqli_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _query(request: httpx.Request) -> str:
    return urllib.parse.unquote(request.url.query.decode())


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_eight_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 8
        assert {p["technique"] for p in payloads} == {"error", "union", "time", "boolean"}


class TestMakeMarker:
    def test_markers_are_unique_and_prefixed(self):
        assert _make_marker() != _make_marker()
        assert _make_marker().startswith("XBOW_PROBE_")


class TestSQLiScannerRegistration:
    def test_registered_under_sqli_scanner(self):
        assert SCANNER_REGISTRY["sqli_scanner"] is SQLiScanner


_ERROR_PAYLOADS = [{"id": "e1", "technique": "error", "payload_template": "'"}]
_UNION_PAYLOADS = [{"id": "u1", "technique": "union", "payload_template": "' UNION SELECT '{marker}'--"}]
_TIME_PAYLOADS = [{"id": "t1", "technique": "time", "payload_template": "' OR SLEEP(5)--", "sleep_seconds": 5}]
_BOOLEAN_PAYLOADS = [
    {"id": "b1", "technique": "boolean", "pair_id": "p1", "boolean_role": "true", "payload_template": "' AND '1'='1"},
    {"id": "b2", "technique": "boolean", "pair_id": "p1", "boolean_role": "false", "payload_template": "' AND '1'='2"},
]


class TestErrorTechnique:
    @pytest.mark.asyncio
    async def test_db_error_signature_produces_candidate(self):
        def handler(request):
            return httpx.Response(200, text=f"Warning: {DB_ERROR_SIGNATURES[0]} near '{_query(request)}'")

        scanner = SQLiScanner(_session(handler), payloads=_ERROR_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert len(candidates) == 1
        assert candidates[0].vuln_type == "sqli"
        assert candidates[0].parameter == "id"
        assert candidates[0].detected_by == "sqli_scanner"

    @pytest.mark.asyncio
    async def test_detection_is_case_insensitive(self):
        def handler(request):
            return httpx.Response(200, text="SQL SYNTAX error near your query")

        scanner = SQLiScanner(_session(handler), payloads=_ERROR_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert len(candidates) == 1

    @pytest.mark.asyncio
    async def test_no_error_signature_produces_no_candidates(self):
        def handler(request):
            return httpx.Response(200, text="Item not found")

        scanner = SQLiScanner(_session(handler), payloads=_ERROR_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_all_documented_signatures_are_individually_detected(self):
        for sig in DB_ERROR_SIGNATURES:
            def handler(request, sig=sig):
                return httpx.Response(200, text=f"error: {sig}")

            scanner = SQLiScanner(_session(handler), payloads=_ERROR_PAYLOADS)
            candidates = await scanner.scan("https://example.com/item?id=5")
            assert len(candidates) == 1, f"signature {sig!r} was not detected"


class TestUnionTechnique:
    @pytest.mark.asyncio
    async def test_marker_reflected_produces_candidate(self):
        def handler(request):
            return httpx.Response(200, text=f"Results: {_query(request)}")

        scanner = SQLiScanner(_session(handler), payloads=_UNION_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert len(candidates) == 1
        assert "XBOW_PROBE_" in candidates[0].payload_used

    @pytest.mark.asyncio
    async def test_marker_not_reflected_produces_no_candidates(self):
        def handler(request):
            return httpx.Response(200, text="Item not found")

        scanner = SQLiScanner(_session(handler), payloads=_UNION_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_two_scans_use_different_markers(self):
        seen = []

        def handler(request):
            seen.append(_query(request))
            return httpx.Response(200, text=_query(request))

        scanner = SQLiScanner(_session(handler), payloads=_UNION_PAYLOADS)
        await scanner.scan("https://example.com/item?id=5")
        await scanner.scan("https://example.com/item?id=5")
        assert seen[0] != seen[1]


class TestTimeTechnique:
    @pytest.mark.asyncio
    async def test_slow_response_produces_candidate(self):
        """Injected clock: baseline takes ~0s, payload request takes ~5s."""
        clock = iter([0.0, 0.1, 0.1, 5.15])  # baseline_start, baseline_end, payload_start, payload_end

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = SQLiScanner(_session(handler), payloads=_TIME_PAYLOADS, time_fn=lambda: next(clock))
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert len(candidates) == 1
        assert candidates[0].parameter == "id"

    @pytest.mark.asyncio
    async def test_fast_response_produces_no_candidates(self):
        clock = iter([0.0, 0.1, 0.1, 0.25])  # payload took ~0.15s -- nowhere near 5s

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = SQLiScanner(_session(handler), payloads=_TIME_PAYLOADS, time_fn=lambda: next(clock))
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_baseline_fetched_exactly_once_regardless_of_param_count(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        clock = iter([0.0, 0.05] + [0.05, 0.1] * 10)  # generous supply
        scanner = SQLiScanner(_session(handler), payloads=_TIME_PAYLOADS, time_fn=lambda: next(clock))
        await scanner.scan("https://example.com/item?id=5&page=2")
        # 1 baseline + 2 payload requests (one per param) = 3 total
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_query_params_makes_no_requests_at_all(self):
        """Regression check for the baseline-fires-even-with-nothing-to-test
        bug caught and fixed before this file was delivered."""
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, text="ok")

        scanner = SQLiScanner(_session(handler), payloads=_TIME_PAYLOADS, time_fn=lambda: 0.0)
        candidates = await scanner.scan("https://example.com/item")
        assert candidates == []
        assert calls == []


class TestBooleanTechnique:
    @pytest.mark.asyncio
    async def test_differing_responses_produce_candidate(self):
        def handler(request):
            q = _query(request)
            body = "match found: extra content here" if "'1'" in q and "1'='1" in q else "no match"
            return httpx.Response(200, text=body)

        scanner = SQLiScanner(_session(handler), payloads=_BOOLEAN_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert len(candidates) == 1
        assert candidates[0].parameter == "id"

    @pytest.mark.asyncio
    async def test_identical_responses_produce_no_candidates(self):
        def handler(request):
            return httpx.Response(200, text="always the same page")

        scanner = SQLiScanner(_session(handler), payloads=_BOOLEAN_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_differing_status_code_alone_produces_candidate(self):
        def handler(request):
            q = _query(request)
            status = 200 if "1'='1" in q else 404
            return httpx.Response(status, text="same length body")

        scanner = SQLiScanner(_session(handler), payloads=_BOOLEAN_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert len(candidates) == 1

    @pytest.mark.asyncio
    async def test_incomplete_pair_is_skipped_not_an_error(self):
        incomplete = [_BOOLEAN_PAYLOADS[0]]  # "true" role only, no matching "false"

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = SQLiScanner(_session(handler), payloads=incomplete)
        candidates = await scanner.scan("https://example.com/item?id=5")  # must not raise
        assert candidates == []

    @pytest.mark.asyncio
    async def test_two_requests_made_per_param_per_pair(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = SQLiScanner(_session(handler), payloads=_BOOLEAN_PAYLOADS)
        await scanner.scan("https://example.com/item?id=5&page=2")
        assert call_count == 4  # 2 params x (true + false)


class TestSQLiScannerIntegration:
    @pytest.mark.asyncio
    async def test_no_query_params_returns_empty_across_all_techniques(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, text="ok")

        scanner = SQLiScanner(_session(handler))  # real payload file, all 4 techniques
        candidates = await scanner.scan("https://example.com/item")
        assert candidates == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_out_of_scope_url_raises(self):
        from core.http.rate_limited_client import OutOfScopeError

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = SQLiScanner(_session(handler), payloads=_ERROR_PAYLOADS)
        with pytest.raises(OutOfScopeError):
            await scanner.scan("https://evil.com/item?id=5")

    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            return httpx.Response(200, text=f"{DB_ERROR_SIGNATURES[0]}{'x' * 1000}")

        scanner = SQLiScanner(_session(handler), payloads=_ERROR_PAYLOADS)
        candidates = await scanner.scan("https://example.com/item?id=5")
        assert len(candidates[0].raw_response_snapshot) == 512
