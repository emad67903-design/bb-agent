"""
Implements: test coverage for core/scanners/cmd_injection.py (Section 7.8).
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
from core.scanners.cmd_injection import CmdInjectionScanner, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="cmd_injection",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _interactsh_client(
    *,
    mode: InteractshMode = InteractshMode.PUBLIC,
    always_received: bool = False,
) -> InteractshClient:
    """See test_ssrf_scanner.py's identical helper for the rationale
    (real InteractshClient, instant sleep_fn, controllable poll_check_fn)."""

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


_ONE_PAYLOAD = [{"id": "semicolon_unix", "technique": "oob", "separator": ";", "os": "unix", "payload_template": "; ping {oob_url} -c 1"}]
_TWO_PAYLOADS = _ONE_PAYLOAD + [
    {"id": "pipe_unix", "technique": "oob", "separator": "|", "os": "unix", "payload_template": "| ping {oob_url} -c 1"}
]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_eight_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 8
        assert {p["separator"] for p in payloads} == {";", "|", "&&", "\n"}
        assert {p["os"] for p in payloads} == {"unix", "windows"}
        assert all(p["technique"] == "oob" for p in payloads)

    def test_blueprint_literal_example_present_verbatim(self):
        """Section 7.8: '; ping {interactsh_url} -c 1' -- the
        semicolon_unix entry, with {interactsh_url} renamed to
        {oob_url} to match ssrf_scanner.py's established template
        placeholder name."""
        payloads = _load_payloads()
        entry = next(p for p in payloads if p["id"] == "semicolon_unix")
        assert entry["payload_template"] == "; ping {oob_url} -c 1"

    def test_no_destructive_payloads(self):
        """Section 10.1 TIER_C_RULES: 'CMDi OOB interactsh ping only
        (no destructive command)'."""
        payloads = _load_payloads()
        assert all("ping" in p["payload_template"] for p in payloads)
        destructive_markers = ("rm ", "del ", "format", "shutdown", "reboot", ">")
        assert not any(m in p["payload_template"] for p in payloads for m in destructive_markers)


class TestCmdInjectionScannerRegistration:
    def test_registered_under_cmd_injection(self):
        assert SCANNER_REGISTRY["cmd_injection"] is CmdInjectionScanner


class TestCmdInjectionScannerNoQueryParams:
    @pytest.mark.asyncio
    async def test_no_query_params_returns_empty(self):
        client = _interactsh_client(always_received=True)

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = CmdInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/no-params")
        assert candidates == []


class TestCmdInjectionScannerOOBUnavailable:
    @pytest.mark.asyncio
    async def test_no_interactsh_client_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = CmdInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=None)
        candidates = await scanner.scan("https://example.com/exec?cmd=ls")
        assert candidates == []
        assert call_count == 0  # this scanner has no non-OOB path -- nothing sent at all

    @pytest.mark.asyncio
    async def test_no_interactsh_client_logs_marker(self, caplog):
        def handler(request):
            return httpx.Response(200, text="ok")

        with caplog.at_level(logging.INFO):
            scanner = CmdInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=None)
            await scanner.scan("https://example.com/exec?cmd=ls")
        assert "[CMD_INJECTION_OOB_UNAVAILABLE]" in caplog.text

    @pytest.mark.asyncio
    async def test_client_mode_unavailable_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        client = _interactsh_client(mode=InteractshMode.UNAVAILABLE)
        scanner = CmdInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/exec?cmd=ls")
        assert candidates == []


class TestCmdInjectionScannerDetection:
    @pytest.mark.asyncio
    async def test_callback_received_produces_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = CmdInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/exec?cmd=ls")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "cmd_injection"
        assert candidate.parameter == "cmd"
        assert candidate.detected_by == "cmd_injection"
        assert candidate.http_method == "GET"
        assert candidate.payload_used.startswith("; ping XBOW_test-session_")
        assert candidate.payload_used.endswith("-c 1")
        assert candidate.probe_correlation_id is not None
        assert ".interactsh.com" not in candidate.probe_correlation_id
        assert candidate.raw_response_snapshot == "accepted"

    @pytest.mark.asyncio
    async def test_no_callback_received_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=False)
        scanner = CmdInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/exec?cmd=ls")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_multiple_payload_variants_each_get_distinct_correlation_ids(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = CmdInjectionScanner(_session(handler), payloads=_TWO_PAYLOADS, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/exec?cmd=ls")

        assert len(candidates) == 2  # one candidate per payload variant, same parameter
        assert {c.parameter for c in candidates} == {"cmd"}
        correlation_ids = {c.probe_correlation_id for c in candidates}
        assert len(correlation_ids) == 2

    @pytest.mark.asyncio
    async def test_multiple_parameters_and_variants_produce_full_cross_product(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = CmdInjectionScanner(_session(handler), payloads=_TWO_PAYLOADS, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/exec?cmd=ls&target=host")

        assert len(candidates) == 4  # 2 parameters x 2 payload variants
        assert {c.parameter for c in candidates} == {"cmd", "target"}


class TestCmdInjectionScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        long_body = "B" * 2000

        def handler(request):
            return httpx.Response(202, text=long_body)

        client = _interactsh_client(always_received=True)
        scanner = CmdInjectionScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/exec?cmd=ls")
        assert len(candidates[0].raw_response_snapshot) == 512


class TestCmdInjectionScannerViaCreateScanner:
    @pytest.mark.asyncio
    async def test_create_scanner_threads_interactsh_client_through(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = create_scanner(
            "cmd_injection",
            scope_domains=["example.com"],
            payloads=_ONE_PAYLOAD,
            interactsh_client=client,
        )
        assert isinstance(scanner, CmdInjectionScanner)
        assert scanner._interactsh_client is client
