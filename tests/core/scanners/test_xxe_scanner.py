"""
Implements: test coverage for core/scanners/xxe_scanner.py (Section 7.9).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import logging

import httpx
import pytest

from core.http.interactsh_client import InteractshClient
from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.enums import InteractshMode
from core.scanners.registry import SCANNER_REGISTRY, create_scanner
from core.scanners.xxe_scanner import XXEScanner, _load_payloads


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="xxe_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _interactsh_client(
    *,
    mode: InteractshMode = InteractshMode.PUBLIC,
    always_received: bool = False,
) -> InteractshClient:
    """See test_ssrf_scanner.py's identical helper for the rationale."""

    async def no_sleep(seconds: float) -> None:
        return None

    async def poll_check_fn(session, correlation_id: str) -> bool:
        return always_received

    dummy_transport = httpx.MockTransport(lambda request: httpx.Response(200))
    dummy_session = RateLimitedClient(
        scope_domains=["*.interactsh.com"],
        caller_id="interactsh_client",
        intercepting_client=InterceptingClient(transport=dummy_transport),
    )
    client = InteractshClient(
        dummy_session,
        session_id="test-session",
        sleep_fn=no_sleep,
        poll_check_fn=poll_check_fn,
    )
    client.mode = mode
    return client


_OOB_ONLY = [
    {
        "id": "oob_dtd_callback",
        "technique": "oob",
        "payload_template": '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://{oob_url}/">]><foo>&xxe;</foo>',
    }
]
_HOSTNAME_ONLY = [
    {
        "id": "etc_hostname_file_read",
        "technique": "in_band_file_read",
        "payload": '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/hostname">]><foo>&xxe;</foo>',
    }
]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_two_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 2
        assert {p["technique"] for p in payloads} == {"oob", "in_band_file_read"}

    def test_oob_entry_is_dtd_external_entity(self):
        payloads = _load_payloads()
        entry = next(p for p in payloads if p["technique"] == "oob")
        assert "<!ENTITY" in entry["payload_template"]
        assert "SYSTEM" in entry["payload_template"]
        assert "{oob_url}" in entry["payload_template"]

    def test_in_band_entry_targets_etc_hostname(self):
        payloads = _load_payloads()
        entry = next(p for p in payloads if p["technique"] == "in_band_file_read")
        assert "file:///etc/hostname" in entry["payload"]


class TestXXEScannerRegistration:
    def test_registered_under_xxe_scanner(self):
        assert SCANNER_REGISTRY["xxe_scanner"] is XXEScanner


class TestXXEScannerNoQueryParamPrecondition:
    @pytest.mark.asyncio
    async def test_attempts_scan_even_with_no_query_parameters(self):
        """Unlike every other scanner built so far -- see module
        docstring's 'WHOLE-XML-BODY TECHNIQUE' note."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = XXEScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        await scanner.scan("https://example.com/upload")  # no query string at all
        assert call_count == 2  # baseline GET + one POST probe -- not short-circuited to []

    @pytest.mark.asyncio
    async def test_no_payloads_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = XXEScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/upload")
        assert candidates == []
        assert call_count == 0


class TestXXEScannerInBandHostname:
    @pytest.mark.asyncio
    async def test_hostname_like_content_produces_candidate(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, text="web-server-01")
            return httpx.Response(200, text="<html>normal page</html>")  # baseline GET

        scanner = XXEScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        candidates = await scanner.scan("https://example.com/upload")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "xxe"
        assert candidate.http_method == "POST"
        assert candidate.parameter is None  # see module docstring
        assert candidate.detected_by == "xxe_scanner"
        assert candidate.probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_content_type_header_is_xml(self):
        captured_content_type = None

        def handler(request):
            nonlocal captured_content_type
            if request.method == "POST":
                captured_content_type = request.headers.get("content-type")
                return httpx.Response(200, text="normal")
            return httpx.Response(200, text="normal")

        scanner = XXEScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        await scanner.scan("https://example.com/upload")
        assert captured_content_type == "application/xml"

    @pytest.mark.asyncio
    async def test_non_hostname_content_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="<html>normal page</html>")

        scanner = XXEScanner(_session(handler), payloads=_HOSTNAME_ONLY)
        candidates = await scanner.scan("https://example.com/upload")
        assert candidates == []


class TestXXEScannerOOB:
    @pytest.mark.asyncio
    async def test_no_interactsh_client_skips_oob_but_hostname_check_still_runs(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, text="web-server-01")
            return httpx.Response(200, text="<html>normal page</html>")

        scanner = XXEScanner(
            _session(handler), payloads=_HOSTNAME_ONLY + _OOB_ONLY, interactsh_client=None
        )
        candidates = await scanner.scan("https://example.com/upload")
        assert len(candidates) == 1  # the hostname candidate only
        assert candidates[0].probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_no_interactsh_client_logs_marker(self, caplog):
        def handler(request):
            return httpx.Response(200, text="ok")

        with caplog.at_level(logging.INFO):
            scanner = XXEScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=None)
            await scanner.scan("https://example.com/upload")
        assert "[XXE_OOB_UNAVAILABLE]" in caplog.text

    @pytest.mark.asyncio
    async def test_client_mode_unavailable_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        client = _interactsh_client(mode=InteractshMode.UNAVAILABLE)
        scanner = XXEScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/upload")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_callback_received_produces_candidate_with_bare_correlation_id(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = XXEScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/upload")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.parameter is None
        assert candidate.http_method == "POST"
        assert "XBOW_test-session_" in candidate.payload_used
        assert candidate.probe_correlation_id is not None
        assert ".interactsh.com" not in candidate.probe_correlation_id
        assert candidate.probe_correlation_id in candidate.payload_used

    @pytest.mark.asyncio
    async def test_no_callback_received_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=False)
        scanner = XXEScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/upload")
        assert candidates == []


class TestXXEScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        # looks_like_hostname_content's own <=253-char check would reject
        # a 2000-char body outright, so the OOB path (no such filter) is
        # what actually exercises truncation here.
        long_body = "C" * 2000

        def handler(request):
            return httpx.Response(202, text=long_body)

        client = _interactsh_client(always_received=True)
        scanner = XXEScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/upload")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestXXEScannerViaCreateScanner:
    @pytest.mark.asyncio
    async def test_create_scanner_threads_interactsh_client_through(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = create_scanner(
            "xxe_scanner",
            scope_domains=["example.com"],
            payloads=_OOB_ONLY,
            interactsh_client=client,
        )
        assert isinstance(scanner, XXEScanner)
        assert scanner._interactsh_client is client
