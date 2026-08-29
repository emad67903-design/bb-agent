"""
Implements: test coverage for core/scanners/prototype_pollution.py (Section 7.15).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.prototype_pollution import (
    PrototypePollutionScanner,
    _append_query_param,
    _load_payloads,
)
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="prototype_pollution",
        intercepting_client=InterceptingClient(transport=transport),
    )


_ONE_PAYLOAD = [{"id": "dunder_proto", "key_template": "__proto__[xbow_probe]"}]


class TestAppendQueryParam:
    def test_preserves_existing_params(self):
        result = _append_query_param("https://example.com/api?a=1&b=2", "new_key", "val")
        assert "a=1" in result
        assert "b=2" in result
        assert "new_key=val" in result

    def test_works_with_no_existing_query_string(self):
        result = _append_query_param("https://example.com/api", "new_key", "val")
        assert result == "https://example.com/api?new_key=val"

    def test_brackets_are_percent_encoded(self):
        result = _append_query_param("https://example.com/api", "__proto__[x]", "1")
        assert "%5B" in result
        assert "%5D" in result
        assert "[" not in result.split("?", 1)[1]


class TestLoadPayloads:
    def test_real_payload_file_has_both_techniques(self):
        payloads = _load_payloads()
        assert {p["id"] for p in payloads} == {"dunder_proto", "constructor_prototype"}

    def test_key_templates_match_expected_syntax(self):
        payloads = _load_payloads()
        proto_entry = next(p for p in payloads if p["id"] == "dunder_proto")
        ctor_entry = next(p for p in payloads if p["id"] == "constructor_prototype")
        assert proto_entry["key_template"] == "__proto__[xbow_probe]"
        assert ctor_entry["key_template"] == "constructor[prototype][xbow_probe]"


class TestPrototypePollutionScannerRegistration:
    def test_registered_under_prototype_pollution(self):
        assert SCANNER_REGISTRY["prototype_pollution"] is PrototypePollutionScanner


class TestPrototypePollutionScannerDetection:
    @pytest.mark.asyncio
    async def test_no_precondition_on_existing_query_params(self):
        """See module docstring's 'A NEW INJECTION SHAPE' note --
        unlike every substitution-based scanner, this one attempts even
        with no existing query string at all."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = PrototypePollutionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        await scanner.scan("https://example.com/api")  # no query string
        assert call_count == 2  # polluted request + clean follow-up

    @pytest.mark.asyncio
    async def test_marker_leaking_into_followup_produces_candidate(self):
        # Simulate a vulnerable target: once polluted, EVERY subsequent
        # response (even to a clean request) echoes the polluted value.
        polluted_marker = {}

        def handler(request):
            query = request.url.query.decode()
            if "__proto__" in query:
                marker = query.split("=")[-1]
                polluted_marker["value"] = marker
                return httpx.Response(200, text="polluted")
            if "value" in polluted_marker:
                return httpx.Response(200, text=f"state: {polluted_marker['value']}")
            return httpx.Response(200, text="clean")

        scanner = PrototypePollutionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/api")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "prototype_pollution"
        assert candidate.parameter == "__proto__[xbow_probe]"
        assert candidate.detected_by == "prototype_pollution"
        assert candidate.http_method == "GET"
        assert candidate.probe_correlation_id is None
        assert candidate.payload_used.startswith("__proto__[xbow_probe]=")

    @pytest.mark.asyncio
    async def test_no_leak_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="clean, no pollution effect")

        scanner = PrototypePollutionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/api")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_each_probe_uses_a_distinct_random_marker(self):
        seen_markers = []

        def handler(request):
            query = request.url.query.decode()
            if "__proto__" in query:
                seen_markers.append(query.split("=")[-1])
            return httpx.Response(200, text="ok")

        scanner = PrototypePollutionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        await scanner.scan("https://example.com/api")
        await scanner.scan("https://example.com/api")
        assert len(seen_markers) == 2
        assert seen_markers[0] != seen_markers[1]


class TestPrototypePollutionScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        captured_marker = {}

        def handler(request):
            query = request.url.query.decode()
            if "__proto__" in query:
                captured_marker["value"] = query.split("=")[-1]
                return httpx.Response(200, text="polluted")
            marker = captured_marker.get("value", "")
            return httpx.Response(200, text=f"leaked:{marker}" + ("H" * 2000))

        scanner = PrototypePollutionScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/api")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestPrototypePollutionScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("prototype_pollution", scope_domains=["example.com"])
        assert isinstance(scanner, PrototypePollutionScanner)
