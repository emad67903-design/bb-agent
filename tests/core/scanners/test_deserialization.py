"""
Implements: test coverage for core/scanners/deserialization.py (Section 7.16).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import base64
import logging
import pickle
import _socket

import httpx
import pytest

from core.http.interactsh_client import InteractshClient
from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.enums import InteractshMode
from core.scanners.deserialization import (
    PICKLE_OOB_PLACEHOLDER,
    DeserializationScanner,
    _load_payloads,
    _render_pickle_payload,
)
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="deserialization",
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


def _make_template_b64() -> str:
    """Builds a fresh placeholder-bearing pickle template, independent
    of the real payload file -- tests should not depend on the exact
    bytes committed in data/payloads/deserialization_payloads.json."""

    class _Probe:
        def __reduce__(self):
            return (_socket.gethostbyname, (PICKLE_OOB_PLACEHOLDER,))

    return base64.b64encode(pickle.dumps(_Probe(), protocol=0)).decode("ascii")


_ONE_PAYLOAD = [
    {"id": "python_pickle_dns_probe", "technique": "oob", "language": "python", "payload_template_b64": _make_template_b64()}
]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_one_entry(self):
        payloads = _load_payloads()
        assert len(payloads) == 1
        assert payloads[0]["language"] == "python"
        assert payloads[0]["technique"] == "oob"

    def test_no_java_or_php_entries(self):
        """See module docstring's 'LANGUAGE COVERAGE' note -- a real,
        flagged gap, confirmed here rather than just asserted in prose."""
        payloads = _load_payloads()
        assert {p["language"] for p in payloads} == {"python"}


class TestRenderPicklePayload:
    def test_substitutes_placeholder_and_produces_working_gadget(self):
        template = _make_template_b64()
        rendered_b64 = _render_pickle_payload(template, "XBOW_unit_abc123.interactsh.com")
        rendered_bytes = base64.b64decode(rendered_b64)

        calls = []
        real = _socket.gethostbyname
        _socket.gethostbyname = lambda h: calls.append(h) or "0.0.0.0"
        try:
            pickle.loads(rendered_bytes)
        finally:
            _socket.gethostbyname = real

        assert calls == ["XBOW_unit_abc123.interactsh.com"]

    def test_no_rce_calls_only_gethostbyname(self):
        """Confirms the gadget invokes exactly one function
        (gethostbyname) and nothing resembling code execution, file
        access, or subprocess spawning -- Section 7.16: 'No RCE
        gadget'."""
        template = _make_template_b64()
        rendered_bytes = base64.b64decode(_render_pickle_payload(template, "XBOW_unit_abc123.interactsh.com"))

        import pickletools

        opcodes = [op.name for op, _arg, _pos in pickletools.genops(rendered_bytes)]
        assert "GLOBAL" in opcodes
        assert "REDUCE" in opcodes
        # No opcodes indicating import machinery beyond the single
        # GLOBAL reference, no STACK_GLOBAL abuse, no BUILD (which
        # could indicate __setstate__-based richer gadgets).
        assert opcodes.count("GLOBAL") == 1

    def test_handles_varying_hostname_lengths(self):
        """See docs/DECISIONS.md item 86: verified against 3
        different-length hostnames before this design was accepted."""
        template = _make_template_b64()
        for host in ("short.interactsh.com", "XBOW_medium-length-id_deadbeef.interactsh.com", "XBOW_" + ("x" * 80) + ".interactsh.com"):
            rendered_bytes = base64.b64decode(_render_pickle_payload(template, host))
            calls = []
            real = _socket.gethostbyname
            _socket.gethostbyname = lambda h: calls.append(h) or "0.0.0.0"
            try:
                pickle.loads(rendered_bytes)
            finally:
                _socket.gethostbyname = real
            assert calls == [host]


class TestDeserializationScannerRegistration:
    def test_registered_under_deserialization(self):
        assert SCANNER_REGISTRY["deserialization"] is DeserializationScanner


class TestDeserializationScannerNoQueryParams:
    @pytest.mark.asyncio
    async def test_no_query_params_returns_empty(self):
        client = _interactsh_client(always_received=True)

        def handler(request):
            return httpx.Response(200, text="ok")

        scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/no-params")
        assert candidates == []


class TestDeserializationScannerOOBUnavailable:
    @pytest.mark.asyncio
    async def test_no_interactsh_client_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=None)
        candidates = await scanner.scan("https://example.com/load?data=x")
        assert candidates == []
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_no_interactsh_client_logs_marker(self, caplog):
        def handler(request):
            return httpx.Response(200, text="ok")

        with caplog.at_level(logging.INFO):
            scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=None)
            await scanner.scan("https://example.com/load?data=x")
        assert "[DESERIALIZATION_OOB_UNAVAILABLE]" in caplog.text

    @pytest.mark.asyncio
    async def test_client_mode_unavailable_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="ok")

        client = _interactsh_client(mode=InteractshMode.UNAVAILABLE)
        scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/load?data=x")
        assert candidates == []


class TestDeserializationScannerDetection:
    @pytest.mark.asyncio
    async def test_callback_received_produces_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/load?data=x")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "deserialization"
        assert candidate.parameter == "data"
        assert candidate.detected_by == "deserialization"
        assert candidate.http_method == "GET"
        assert candidate.probe_correlation_id is not None
        assert ".interactsh.com" not in candidate.probe_correlation_id
        # payload_used is base64 -- decodes to valid pickle bytes containing the correlation id
        decoded = base64.b64decode(candidate.payload_used)
        assert candidate.probe_correlation_id.encode("ascii") in decoded

    @pytest.mark.asyncio
    async def test_no_callback_received_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=False)
        scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/load?data=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_multiple_parameters_get_distinct_correlation_ids(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/load?data=x&session=y")

        assert len(candidates) == 2
        assert {c.parameter for c in candidates} == {"data", "session"}
        assert len({c.probe_correlation_id for c in candidates}) == 2


class TestDeserializationScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        long_body = "D" * 2000

        def handler(request):
            return httpx.Response(202, text=long_body)

        client = _interactsh_client(always_received=True)
        scanner = DeserializationScanner(_session(handler), payloads=_ONE_PAYLOAD, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/load?data=x")
        assert len(candidates[0].raw_response_snapshot) == 512


class TestDeserializationScannerViaCreateScanner:
    @pytest.mark.asyncio
    async def test_create_scanner_threads_interactsh_client_through(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(always_received=True)
        scanner = create_scanner(
            "deserialization",
            scope_domains=["example.com"],
            payloads=_ONE_PAYLOAD,
            interactsh_client=client,
        )
        assert isinstance(scanner, DeserializationScanner)
        assert scanner._interactsh_client is client
