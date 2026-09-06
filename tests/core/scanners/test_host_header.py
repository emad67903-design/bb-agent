"""
Implements: test coverage for core/scanners/host_header.py (Section 7.20).
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
from core.scanners.host_header import HostHeaderScanner, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="host_header",
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


_IN_BAND_ONLY = [{"id": "host_reflection_marker", "technique": "in_band_reflection", "payload_template": "xbow-hh-{nonce}.example"}]
_OOB_ONLY = [{"id": "oob_callback", "technique": "oob", "payload_template": "{oob_url}"}]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_two_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 2
        assert {p["technique"] for p in payloads} == {"in_band_reflection", "oob"}


class TestHostHeaderScannerRegistration:
    def test_registered_under_host_header(self):
        assert SCANNER_REGISTRY["host_header"] is HostHeaderScanner


class TestHostHeaderScannerNoPreconditionOnQueryParams:
    @pytest.mark.asyncio
    async def test_attempts_scan_even_with_no_query_parameters(self):
        """No parameter/query-string precondition -- see module
        docstring; matches item 69's pre-existing Host Header
        classification."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = HostHeaderScanner(_session(handler), payloads=_IN_BAND_ONLY)
        await scanner.scan("https://example.com/reset-password")  # no query string
        assert call_count == 2  # baseline + one probe

    @pytest.mark.asyncio
    async def test_no_payloads_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = HostHeaderScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/reset-password")
        assert candidates == []
        assert call_count == 0


class TestHostHeaderScannerInBandReflection:
    @pytest.mark.asyncio
    async def test_host_actually_overridden_on_the_wire(self):
        captured_hosts = []

        def handler(request):
            captured_hosts.append(request.headers.get("host"))
            return httpx.Response(200, text="ok")

        scanner = HostHeaderScanner(_session(handler), payloads=_IN_BAND_ONLY)
        await scanner.scan("https://example.com/reset-password")
        # First call = baseline (real example.com host), second = the
        # overridden probe host.
        assert captured_hosts[0] == "example.com"
        assert captured_hosts[1].startswith("xbow-hh-")
        assert captured_hosts[1].endswith(".example")

    @pytest.mark.asyncio
    async def test_reflected_marker_produces_candidate(self):
        def handler(request):
            host = request.headers.get("host")
            if host and host.startswith("xbow-hh-"):
                return httpx.Response(200, text=f"Error: invalid request for host {host}")
            return httpx.Response(200, text="<html>normal page</html>")

        scanner = HostHeaderScanner(_session(handler), payloads=_IN_BAND_ONLY)
        candidates = await scanner.scan("https://example.com/reset-password")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "host_header"
        assert candidate.parameter is None
        assert candidate.http_method == "GET"
        assert candidate.detected_by == "host_header"
        assert candidate.payload_used.startswith("xbow-hh-")
        assert candidate.payload_used in candidate.raw_response_snapshot
        assert candidate.probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_no_reflection_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="<html>normal page, no reflection</html>")

        scanner = HostHeaderScanner(_session(handler), payloads=_IN_BAND_ONLY)
        candidates = await scanner.scan("https://example.com/reset-password")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_each_probe_uses_a_distinct_random_marker(self):
        """False-positive avoidance -- see module docstring's
        comparison to xss_scanner.py's own marker convention."""
        seen_hosts = []

        def handler(request):
            host = request.headers.get("host")
            if host and host.startswith("xbow-hh-"):
                seen_hosts.append(host)
            return httpx.Response(200, text="ok")

        scanner = HostHeaderScanner(_session(handler), payloads=_IN_BAND_ONLY)
        await scanner.scan("https://example.com/reset-password")
        await scanner.scan("https://example.com/reset-password")
        assert len(seen_hosts) == 2
        assert seen_hosts[0] != seen_hosts[1]


class TestHostHeaderScannerOOB:
    @pytest.mark.asyncio
    async def test_no_interactsh_client_skips_oob_but_in_band_still_runs(self):
        def handler(request):
            host = request.headers.get("host")
            if host and host.startswith("xbow-hh-"):
                return httpx.Response(200, text=f"seen {host}")
            return httpx.Response(200, text="normal")

        scanner = HostHeaderScanner(
            _session(handler), payloads=_IN_BAND_ONLY + _OOB_ONLY, interactsh_client=None
        )
        candidates = await scanner.scan("https://example.com/reset-password")
        assert len(candidates) == 1  # in-band candidate only
        assert candidates[0].probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_no_interactsh_client_logs_marker(self, caplog):
        def handler(request):
            return httpx.Response(200, text="ok")

        with caplog.at_level(logging.INFO):
            scanner = HostHeaderScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=None)
            await scanner.scan("https://example.com/reset-password")
        assert "[HOST_HEADER_OOB_UNAVAILABLE]" in caplog.text

    @pytest.mark.asyncio
    async def test_client_mode_unavailable_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        client = _interactsh_client(mode=InteractshMode.UNAVAILABLE)
        scanner = HostHeaderScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/reset-password")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_callback_received_produces_candidate_with_bare_correlation_id(self):
        captured_hosts = []

        def handler(request):
            captured_hosts.append(request.headers.get("host"))
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = HostHeaderScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/reset-password")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.parameter is None
        # Host header value is the bare oob_url -- no "http://" prefix,
        # unlike ssrf_scanner.py/cmd_injection.py's URL-shaped payloads.
        assert candidate.payload_used.startswith("XBOW_test-session_")
        assert candidate.payload_used.endswith(".interactsh.com")
        assert not candidate.payload_used.startswith("http")
        assert captured_hosts[0] == candidate.payload_used
        assert candidate.probe_correlation_id is not None
        assert ".interactsh.com" not in candidate.probe_correlation_id

    @pytest.mark.asyncio
    async def test_no_callback_received_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=False)
        scanner = HostHeaderScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/reset-password")
        assert candidates == []


class TestHostHeaderScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            host = request.headers.get("host")
            if host and host.startswith("xbow-hh-"):
                return httpx.Response(200, text=f"Error: invalid host {host}" + ("E" * 2000))
            return httpx.Response(200, text="short baseline")

        scanner = HostHeaderScanner(_session(handler), payloads=_IN_BAND_ONLY)
        candidates = await scanner.scan("https://example.com/reset-password")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestHostHeaderScannerViaCreateScanner:
    @pytest.mark.asyncio
    async def test_create_scanner_threads_interactsh_client_through(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = create_scanner(
            "host_header",
            scope_domains=["example.com"],
            payloads=_OOB_ONLY,
            interactsh_client=client,
        )
        assert isinstance(scanner, HostHeaderScanner)
        assert scanner._interactsh_client is client
