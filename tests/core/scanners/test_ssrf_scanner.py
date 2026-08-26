"""
Implements: test coverage for core/scanners/ssrf_scanner.py (Section 7.3).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from core.http.interactsh_client import InteractshClient
from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.enums import InteractshMode
from core.scanners.registry import SCANNER_REGISTRY, create_scanner
from core.scanners.ssrf_scanner import (
    SSRFScanner,
    _load_payloads,
    _matches_azure_signal,
    _matches_gcp_signal,
)


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="ssrf_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _path_and_query(request: httpx.Request) -> str:
    return f"{request.url.path}?{urllib.parse.unquote(request.url.query.decode())}"


def _interactsh_client(
    *,
    mode: InteractshMode = InteractshMode.PUBLIC,
    always_received: bool = False,
    env_dependent: bool = False,
) -> InteractshClient:
    """A real `InteractshClient`, instant `sleep_fn`, controllable
    `poll_check_fn` -- same injection-at-the-true-boundary philosophy
    `test_interactsh_client.py` itself already established, not a fake
    stand-in class for the whole component (satisfies the
    `InteractshClient` type hint exactly, and re-exercises the real
    `poll()` state machine rather than a re-implementation of it).
    """

    async def no_sleep(seconds: float) -> None:
        return None

    async def poll_check_fn(session, correlation_id: str) -> bool:
        if env_dependent:
            raise httpx.TransportError("simulated transport failure")
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


_AWS_ONLY = [
    {
        "id": "aws_imdsv1_ami_id",
        "technique": "in_band_metadata",
        "cloud": "aws",
        "payload": "http://169.254.169.254/latest/meta-data/ami-id",
        "expected": "401",
    }
]
_GCP_ONLY = [
    {
        "id": "gcp_project_id",
        "technique": "in_band_metadata",
        "cloud": "gcp",
        "payload": "http://metadata.google.internal/computeMetadata/v1/project/project-id",
        "expected": "computeMetadata marker",
    }
]
_AZURE_ONLY = [
    {
        "id": "azure_instance_metadata",
        "technique": "in_band_metadata",
        "cloud": "azure",
        "payload": "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        "expected": "compute/network JSON keys",
    }
]
_OOB_ONLY = [
    {
        "id": "oob_callback",
        "technique": "oob",
        "payload_template": "http://{oob_url}/",
        "expected": "interactsh callback",
    }
]


class TestLoadPayloads:
    def test_real_payload_file_loads_and_has_four_entries(self):
        payloads = _load_payloads()
        assert len(payloads) == 4
        assert {p["technique"] for p in payloads} == {"in_band_metadata", "oob"}
        assert {p["cloud"] for p in payloads if p["technique"] == "in_band_metadata"} == {"aws", "gcp", "azure"}

    def test_no_priority_1_or_2_entries(self):
        """See module docstring's 'PRIORITIES 1+2 DELIBERATELY NOT
        IMPLEMENTED'. Confirms no payload entry references a PUT/token
        flow."""
        payloads = _load_payloads()
        assert not any("token" in p.get("payload", "") for p in payloads)
        assert not any(p.get("cloud") == "aws" and "api/token" in p.get("payload", "") for p in payloads)


class TestMatchesGcpSignal:
    def test_compute_metadata_substring_matches(self):
        assert _matches_gcp_signal('{"computeMetadata": "v1"}') is True

    def test_permission_denied_json_shape_matches(self):
        assert _matches_gcp_signal('{"status": "PERMISSION_DENIED"}') is True

    def test_permission_denied_word_alone_without_status_does_not_match(self):
        """Deliberately narrower than a bare substring check -- see
        module docstring / function docstring."""
        assert _matches_gcp_signal("Access permission_denied for this resource") is False

    def test_unrelated_body_does_not_match(self):
        assert _matches_gcp_signal("<html>404 not found</html>") is False


class TestMatchesAzureSignal:
    def test_quoted_compute_key_matches(self):
        assert _matches_azure_signal('{"compute": {"name": "vm1"}}') is True

    def test_quoted_network_key_matches(self):
        assert _matches_azure_signal('{"network": {"interface": []}}') is True

    def test_unquoted_plain_english_word_does_not_match(self):
        assert _matches_azure_signal("the network connection failed") is False

    def test_unrelated_body_does_not_match(self):
        assert _matches_azure_signal("<html>ok</html>") is False


class TestSSRFScannerRegistration:
    def test_registered_under_ssrf_scanner(self):
        assert SCANNER_REGISTRY["ssrf_scanner"] is SSRFScanner


class TestSSRFScannerNoQueryParams:
    @pytest.mark.asyncio
    async def test_no_query_params_returns_empty_without_any_request(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="ok")

        scanner = SSRFScanner(_session(handler), payloads=_AWS_ONLY)
        candidates = await scanner.scan("https://example.com/no-params")
        assert candidates == []
        assert call_count == 0


class TestSSRFScannerAWSInBand:
    @pytest.mark.asyncio
    async def test_401_with_non_401_baseline_produces_candidate(self):
        def handler(request):
            if "169.254.169.254" in _path_and_query(request):
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(200, text="normal page")

        scanner = SSRFScanner(_session(handler), payloads=_AWS_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert len(candidates) == 1
        assert candidates[0].vuln_type == "ssrf"
        assert candidates[0].parameter == "url"
        assert candidates[0].payload_used == "http://169.254.169.254/latest/meta-data/ami-id"
        assert candidates[0].detected_by == "ssrf_scanner"
        assert candidates[0].probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_401_baseline_also_401_produces_no_candidate(self):
        """Baseline guard -- see module docstring's 'AWS'S 401 CHECK
        GETS A BASELINE GUARD' note. An auth-walled endpoint that
        returns 401 for everything is not an SSRF signal."""

        def handler(request):
            return httpx.Response(401, text="Unauthorized")

        scanner = SSRFScanner(_session(handler), payloads=_AWS_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_200_response_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="normal page")

        scanner = SSRFScanner(_session(handler), payloads=_AWS_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates == []


class TestSSRFScannerGCPInBand:
    @pytest.mark.asyncio
    async def test_compute_metadata_body_produces_candidate(self):
        def handler(request):
            if "metadata.google.internal" in _path_and_query(request):
                return httpx.Response(200, text='{"computeMetadata": "v1 only"}')
            return httpx.Response(200, text="normal page")

        scanner = SSRFScanner(_session(handler), payloads=_GCP_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert len(candidates) == 1
        assert candidates[0].parameter == "url"

    @pytest.mark.asyncio
    async def test_no_marker_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="normal page")

        scanner = SSRFScanner(_session(handler), payloads=_GCP_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates == []


class TestSSRFScannerAzureInBand:
    @pytest.mark.asyncio
    async def test_compute_network_keys_produce_candidate(self):
        def handler(request):
            if "api-version" in _path_and_query(request):
                return httpx.Response(200, text='{"compute": {}, "network": {}}')
            return httpx.Response(200, text="normal page")

        scanner = SSRFScanner(_session(handler), payloads=_AZURE_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert len(candidates) == 1

    @pytest.mark.asyncio
    async def test_no_marker_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(200, text="normal page")

        scanner = SSRFScanner(_session(handler), payloads=_AZURE_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates == []


class TestSSRFScannerOOB:
    @pytest.mark.asyncio
    async def test_no_interactsh_client_skips_oob_but_in_band_still_runs(self):
        def handler(request):
            if "169.254.169.254" in _path_and_query(request) and "latest/meta-data" in _path_and_query(request):
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(200, text="normal page")

        scanner = SSRFScanner(_session(handler), payloads=_AWS_ONLY + _OOB_ONLY, interactsh_client=None)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert len(candidates) == 1  # the AWS in-band candidate only
        assert candidates[0].probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_interactsh_client_logs_marker_when_unavailable(self, caplog):
        import logging

        def handler(request):
            return httpx.Response(200, text="normal page")

        with caplog.at_level(logging.INFO):
            scanner = SSRFScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=None)
            await scanner.scan("https://example.com/fetch?url=x")
        assert "[SSRF_OOB_UNAVAILABLE]" in caplog.text

    @pytest.mark.asyncio
    async def test_client_present_but_mode_unavailable_skips_oob(self):
        def handler(request):
            return httpx.Response(200, text="normal page")

        client = _interactsh_client(mode=InteractshMode.UNAVAILABLE)
        scanner = SSRFScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_callback_received_produces_candidate_with_bare_correlation_id(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(mode=InteractshMode.PUBLIC, always_received=True)
        scanner = SSRFScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/fetch?url=x")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.parameter == "url"
        assert candidate.payload_used.startswith("http://XBOW_test-session_")
        assert candidate.payload_used.endswith(".interactsh.com/")
        # Bare correlation ID, not the full oob_url -- see module
        # docstring's "`probe_correlation_id` HOLDS THE BARE
        # CORRELATION ID" note.
        assert candidate.probe_correlation_id is not None
        assert ".interactsh.com" not in candidate.probe_correlation_id
        assert candidate.probe_correlation_id.startswith("XBOW_test-session_")
        assert candidate.probe_correlation_id in candidate.payload_used

    @pytest.mark.asyncio
    async def test_no_callback_received_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(mode=InteractshMode.PUBLIC, always_received=False)
        scanner = SSRFScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_env_dependent_transport_error_produces_no_candidate(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(mode=InteractshMode.PUBLIC, env_dependent=True)
        scanner = SSRFScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_multiple_parameters_get_distinct_correlation_ids(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(mode=InteractshMode.PUBLIC, always_received=True)
        scanner = SSRFScanner(_session(handler), payloads=_OOB_ONLY, interactsh_client=client)
        candidates = await scanner.scan("https://example.com/fetch?url=x&callback=y")

        assert len(candidates) == 2
        params = {c.parameter for c in candidates}
        assert params == {"url", "callback"}
        correlation_ids = {c.probe_correlation_id for c in candidates}
        assert len(correlation_ids) == 2  # distinct -- not one ID shared across both


class TestSSRFScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        long_body = "A" * 2000

        def handler(request):
            if "169.254.169.254" in _path_and_query(request):
                return httpx.Response(401, text=long_body)
            return httpx.Response(200, text="normal page")  # baseline

        scanner = SSRFScanner(_session(handler), payloads=_AWS_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512

    @pytest.mark.asyncio
    async def test_http_method_is_get(self):
        def handler(request):
            if "169.254.169.254" in _path_and_query(request):
                return httpx.Response(401, text="Unauthorized")
            return httpx.Response(200, text="normal page")  # baseline

        scanner = SSRFScanner(_session(handler), payloads=_AWS_ONLY)
        candidates = await scanner.scan("https://example.com/fetch?url=x")
        assert candidates[0].http_method == "GET"


class TestSSRFScannerViaCreateScanner:
    """End-to-end proof that item 82's `**scanner_kwargs` passthrough
    genuinely threads a real `InteractshClient` into a real scanner --
    not just the synthetic dummy-scanner tests in `test_registry.py`.
    """

    @pytest.mark.asyncio
    async def test_create_scanner_threads_interactsh_client_through(self):
        def handler(request):
            return httpx.Response(202, text="accepted")

        client = _interactsh_client(mode=InteractshMode.PUBLIC, always_received=True)
        scanner = create_scanner(
            "ssrf_scanner",
            scope_domains=["example.com"],
            payloads=_OOB_ONLY,
            interactsh_client=client,
        )
        assert isinstance(scanner, SSRFScanner)
        assert scanner._interactsh_client is client

    def test_create_scanner_without_interactsh_client_still_works(self):
        scanner = create_scanner("ssrf_scanner", scope_domains=["example.com"])
        assert isinstance(scanner, SSRFScanner)
        assert scanner._interactsh_client is None
