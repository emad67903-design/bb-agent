"""
Implements: test coverage for core/scanners/http_smuggling.py (Section 7.21).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.http_smuggling import (
    HTTPSmugglingScanner,
    _load_configs,
    _load_payloads,
    _split_host_and_path,
)
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    """A target-scoped session -- see TestTargetSessionNeverUsed: this
    scanner's own network call never goes through this client at all."""
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="http_smuggling",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _go_session(handler) -> RateLimitedClient:
    """A 127.0.0.1-scoped session, standing in for the real
    `_default_go_service_client()` -- see module docstring's "HOW THIS
    SCANNER REACHES ITS OWN GO SIDECAR" note."""
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["127.0.0.1"],
        caller_id="http_smuggling",
        intercepting_client=InterceptingClient(transport=transport),
    )


_CONFIGS = [
    {"id": "cl_te_timing", "type": "CL.TE", "description": "d1"},
    {"id": "te_cl_timing", "type": "TE.CL", "description": "d2"},
]
_PAYLOADS = [
    {"type": "CL.TE", "raw_request_template": "POST {path} HTTP/1.1\r\nHost: {host}\r\nX: 1\r\n\r\nBODY-CLTE"},
    {"type": "TE.CL", "raw_request_template": "POST {path} HTTP/1.1\r\nHost: {host}\r\nX: 2\r\n\r\nBODY-TECL"},
]


def _go_handler_returning(vulnerable: bool, status_code: int = 200):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            status_code,
            text=json.dumps(
                {
                    "results": [
                        {"type": "CL.TE", "timed_out": vulnerable, "elapsed_ms": 10000 if vulnerable else 42},
                        {"type": "TE.CL", "timed_out": False, "elapsed_ms": 38},
                    ],
                    "vulnerable": vulnerable,
                }
            ),
        )

    return handler, captured


class TestLoadConfigsAndPayloads:
    def test_real_config_file_has_two_variant_types(self):
        configs = _load_configs()
        assert {c["type"] for c in configs} == {"CL.TE", "TE.CL"}

    def test_real_payload_file_has_two_templates_matching_config_types(self):
        payloads = _load_payloads()
        types = {p["type"] for p in payloads}
        assert types == {"CL.TE", "TE.CL"}
        for entry in payloads:
            assert "{host}" in entry["raw_request_template"]
            assert "{path}" in entry["raw_request_template"]

    def test_real_payload_bodies_are_byte_exact_against_declared_content_length(self):
        """Verifies the byte-length claim in http_smuggling_payloads.json's
        own `_status` field, independently of that field's prose."""
        payloads = {p["type"]: p["raw_request_template"] for p in _load_payloads()}

        cl_te = payloads["CL.TE"].format(host="x", path="/")
        body = cl_te.split("\r\n\r\n", 1)[1]
        assert body == "1\r\nA\r\nX"
        assert len(body) == 7  # front-end (Content-Length: 4) forwards only the first 4

        te_cl = payloads["TE.CL"].format(host="x", path="/")
        body = te_cl.split("\r\n\r\n", 1)[1]
        assert body == "0\r\n\r\nX"
        assert len(body) == 6  # matches the declared Content-Length: 6 exactly


class TestSplitHostAndPath:
    def test_https_default_port_omitted(self):
        assert _split_host_and_path("https://example.com/api/x") == ("example.com", "/api/x")

    def test_http_default_port_omitted(self):
        assert _split_host_and_path("http://example.com/api/x") == ("example.com", "/api/x")

    def test_non_default_port_included(self):
        assert _split_host_and_path("https://example.com:8443/x") == ("example.com:8443", "/x")

    def test_no_path_defaults_to_slash(self):
        assert _split_host_and_path("https://example.com") == ("example.com", "/")

    def test_query_string_appended_to_path(self):
        assert _split_host_and_path("https://example.com/x?a=1") == ("example.com", "/x?a=1")


class TestHTTPSmugglingScannerRegistration:
    def test_registered_under_http_smuggling(self):
        assert SCANNER_REGISTRY["http_smuggling"] is HTTPSmugglingScanner

    def test_no_interactsh_client_parameter(self):
        """See module docstring -- Section 7.21 names no OOB technique."""
        import inspect

        sig = inspect.signature(HTTPSmugglingScanner.__init__)
        assert "interactsh_client" not in sig.parameters


class TestTargetSessionNeverUsed:
    @pytest.mark.asyncio
    async def test_self_session_receives_zero_requests(self):
        """Confirms the module docstring's central claim directly, not
        just in prose: this scanner's one network call goes entirely
        through go_service_client, never through self.session."""
        call_count = 0

        def target_handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="should never be called")

        go_handler, _ = _go_handler_returning(vulnerable=True)
        scanner = HTTPSmugglingScanner(
            _session(target_handler),
            configs=_CONFIGS,
            payloads=_PAYLOADS,
            go_service_client=_go_session(go_handler),
        )
        await scanner.scan("https://example.com/api/x")
        assert call_count == 0


class TestHTTPSmugglingScannerDetection:
    @pytest.mark.asyncio
    async def test_vulnerable_true_produces_one_candidate_with_correct_fields(self):
        go_handler, captured = _go_handler_returning(vulnerable=True)
        scanner = HTTPSmugglingScanner(
            _session(lambda r: httpx.Response(200)),
            configs=_CONFIGS,
            payloads=_PAYLOADS,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/x")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "http_smuggling"
        assert candidate.endpoint == "https://example.com/api/x"
        assert candidate.http_method == "POST"
        assert candidate.parameter is None
        assert candidate.detected_by == "http_smuggling"
        assert candidate.probe_correlation_id is None
        assert "CL.TE" in candidate.payload_used
        assert "TE.CL" in candidate.payload_used

        # Wire-contract body shape sent to smuggling_engine.
        assert captured["body"]["target_url"] == "https://example.com/api/x"
        assert len(captured["body"]["variants"]) == 2
        sent_types = {v["type"] for v in captured["body"]["variants"]}
        assert sent_types == {"CL.TE", "TE.CL"}

        # Each raw_request_b64, decoded, is exactly the template with
        # {host}/{path} substituted -- byte-exact, not just "present".
        for v in captured["body"]["variants"]:
            decoded = base64.b64decode(v["raw_request_b64"]).decode("utf-8")
            expected = next(p["raw_request_template"] for p in _PAYLOADS if p["type"] == v["type"])
            assert decoded == expected.format(host="example.com", path="/api/x")

    @pytest.mark.asyncio
    async def test_vulnerable_false_produces_no_candidate(self):
        go_handler, _ = _go_handler_returning(vulnerable=False)
        scanner = HTTPSmugglingScanner(
            _session(lambda r: httpx.Response(200)),
            configs=_CONFIGS,
            payloads=_PAYLOADS,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_go_service_non_200_produces_no_candidate(self):
        go_handler, _ = _go_handler_returning(vulnerable=True, status_code=403)
        scanner = HTTPSmugglingScanner(
            _session(lambda r: httpx.Response(200)),
            configs=_CONFIGS,
            payloads=_PAYLOADS,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "results": [
                            {"type": "CL.TE", "timed_out": True, "elapsed_ms": 10000},
                            {"type": "TE.CL", "timed_out": False, "elapsed_ms": 40},
                        ],
                        "vulnerable": True,
                        "padding": "M" * 2000,
                    }
                ),
            )

        scanner = HTTPSmugglingScanner(
            _session(lambda r: httpx.Response(200)),
            configs=_CONFIGS,
            payloads=_PAYLOADS,
            go_service_client=_go_session(handler),
        )
        candidates = await scanner.scan("https://example.com/api/x")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestHTTPSmugglingScannerEmptyConfigOrPayload:
    @pytest.mark.asyncio
    async def test_empty_configs_returns_empty_without_any_go_call(self):
        call_count = 0

        def go_handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text='{"results": [], "vulnerable": false}')

        scanner = HTTPSmugglingScanner(
            _session(lambda r: httpx.Response(200)),
            configs=[],
            payloads=_PAYLOADS,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/x")
        assert candidates == []
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_empty_payloads_returns_empty_without_any_go_call(self):
        call_count = 0

        def go_handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text='{"results": [], "vulnerable": false}')

        scanner = HTTPSmugglingScanner(
            _session(lambda r: httpx.Response(200)),
            configs=_CONFIGS,
            payloads=[],
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/x")
        assert candidates == []
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_config_type_with_no_matching_payload_template_is_skipped(self):
        """One config entry has no matching payload template -- scan()
        should fall back to <2 variants and return [] without calling
        the Go service, per _build_variants' own skip-not-fatal note."""
        call_count = 0

        def go_handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text='{"results": [], "vulnerable": false}')

        scanner = HTTPSmugglingScanner(
            _session(lambda r: httpx.Response(200)),
            configs=_CONFIGS,
            payloads=[_PAYLOADS[0]],  # only CL.TE has a template
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/x")
        assert candidates == []
        assert call_count == 0


class TestHTTPSmugglingScannerViaCreateScanner:
    def test_create_scanner_builds_working_instance(self):
        scanner = create_scanner("http_smuggling", scope_domains=["example.com"])
        assert isinstance(scanner, HTTPSmugglingScanner)
        assert scanner.session.caller_id == "http_smuggling"
        # go_service_client defaults to a real, independently-scoped client.
        assert scanner._go_service_client.caller_id == "http_smuggling"
