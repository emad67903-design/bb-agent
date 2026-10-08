"""
Implements: test coverage for core/scanners/race_scanner.py (Section 7.5).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.race_scanner import RaceScanner, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    """A target-scoped session -- see TestTargetSessionNeverUsed: this
    scanner's own network call never goes through this client at all."""
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="race_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


def _go_session(handler) -> RateLimitedClient:
    """A 127.0.0.1-scoped session, standing in for the real
    `_default_go_service_client()` -- see module docstring's "HOW THIS
    SCANNER REACHES ITS OWN GO SIDECAR" note."""
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["127.0.0.1"],
        caller_id="race_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


_PAYLOADS = [
    {
        "id": "generic_concurrent_replay",
        "method": "POST",
        "headers": {},
        "body": "",
        "success_status_codes": [200],
    }
]


def _go_handler_returning(success_count: int, total: int = 30, status_code: int = 200):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            status_code,
            text=json.dumps(
                {
                    "results": [{"index": i, "status_code": 200, "elapsed_ms": 5, "start_ns": i} for i in range(total)],
                    "success_count": success_count,
                    "total": total,
                }
            ),
        )

    return handler, captured


class TestLoadPayloads:
    def test_real_payload_file_has_exactly_one_entry(self):
        """Section 7.5 fires one burst of one request -- see module
        docstring's 'ONE PAYLOAD ENTRY, NOT A PER-VARIANT SET' note."""
        payloads = _load_payloads()
        assert len(payloads) == 1

    def test_real_payload_entry_has_the_required_fields(self):
        payloads = _load_payloads()
        entry = payloads[0]
        assert entry["id"]
        assert entry["method"]
        assert isinstance(entry["headers"], dict)
        assert isinstance(entry["body"], str)
        assert entry["success_status_codes"] == [200]


class TestRaceScannerRegistration:
    def test_registered_under_race_scanner(self):
        assert SCANNER_REGISTRY["race_scanner"] is RaceScanner

    def test_no_interactsh_client_parameter(self):
        """See module docstring -- Section 7.5 names no OOB technique."""
        import inspect

        sig = inspect.signature(RaceScanner.__init__)
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

        go_handler, _ = _go_handler_returning(success_count=5)
        scanner = RaceScanner(
            _session(target_handler),
            payloads=_PAYLOADS,
            race_parallel=30,
            go_service_client=_go_session(go_handler),
        )
        await scanner.scan("https://example.com/api/redeem")
        assert call_count == 0


class TestRaceScannerDetection:
    @pytest.mark.asyncio
    async def test_success_count_above_expected_produces_one_candidate_with_correct_fields(self):
        go_handler, captured = _go_handler_returning(success_count=3, total=30)
        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            race_parallel=30,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/redeem")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "race_scanner"
        assert candidate.endpoint == "https://example.com/api/redeem"
        assert candidate.http_method == "POST"
        assert candidate.parameter is None
        assert candidate.detected_by == "race_scanner"
        assert candidate.payload_used == "generic_concurrent_replay"
        assert candidate.probe_correlation_id is None
        assert candidate.success_count == 3
        assert candidate.total_requests == 30

        # Wire-contract body shape sent to race_engine -- byte/field-exact,
        # not just "a request was sent".
        assert captured["body"]["target_url"] == "https://example.com/api/redeem"
        assert captured["body"]["method"] == "POST"
        assert captured["body"]["headers"] == {}
        assert captured["body"]["body"] == ""
        assert captured["body"]["parallel"] == 30
        assert captured["body"]["success_status_codes"] == [200]

    @pytest.mark.asyncio
    async def test_success_count_equal_to_expected_produces_no_candidate(self):
        """Section 7.5: 'Expected: 1 success.' Exactly 1 is the
        non-vulnerable baseline, not a signal."""
        go_handler, _ = _go_handler_returning(success_count=1, total=30)
        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            race_parallel=30,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/redeem")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_success_count_below_expected_produces_no_candidate(self):
        """Edge case: every concurrent request failed (e.g. the
        endpoint rejected all of them) -- 0 is still <= the expected
        baseline of 1, not a race signal."""
        go_handler, _ = _go_handler_returning(success_count=0, total=30)
        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            race_parallel=30,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/redeem")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_go_service_non_200_produces_no_candidate(self):
        go_handler, _ = _go_handler_returning(success_count=5, status_code=403)
        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            race_parallel=30,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/redeem")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "results": [],
                        "success_count": 5,
                        "total": 30,
                        "padding": "M" * 2000,
                    }
                ),
            )

        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            race_parallel=30,
            go_service_client=_go_session(handler),
        )
        candidates = await scanner.scan("https://example.com/api/redeem")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestRaceScannerEmptyPayloads:
    @pytest.mark.asyncio
    async def test_empty_payloads_returns_empty_without_any_go_call(self):
        call_count = 0

        def go_handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text='{"results": [], "success_count": 0, "total": 0}')

        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=[],
            race_parallel=30,
            go_service_client=_go_session(go_handler),
        )
        candidates = await scanner.scan("https://example.com/api/redeem")
        assert candidates == []
        assert call_count == 0


class TestRaceParallelSourcing:
    def test_injected_value_is_stored_and_sent_in_wire_request(self):
        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            race_parallel=7,
            go_service_client=_go_session(lambda r: httpx.Response(200)),
        )
        assert scanner._race_parallel == 7

    def test_defaults_to_load_race_parallel_from_real_scope_yaml_when_not_injected(self):
        """No race_parallel kwarg supplied -- falls back to
        load_race_parallel(configs/scope.yaml), this repo's real file,
        whose documented value (Section 3's comment block) is 30."""
        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            go_service_client=_go_session(lambda r: httpx.Response(200)),
        )
        assert scanner._race_parallel == 30

    @pytest.mark.asyncio
    async def test_injected_value_propagates_into_the_go_request_parallel_field(self):
        go_handler, captured = _go_handler_returning(success_count=5, total=7)
        scanner = RaceScanner(
            _session(lambda r: httpx.Response(200)),
            payloads=_PAYLOADS,
            race_parallel=7,
            go_service_client=_go_session(go_handler),
        )
        await scanner.scan("https://example.com/api/redeem")
        assert captured["body"]["parallel"] == 7


class TestRaceScannerViaCreateScanner:
    def test_create_scanner_builds_working_instance(self):
        scanner = create_scanner("race_scanner", scope_domains=["example.com"])
        assert isinstance(scanner, RaceScanner)
        assert scanner.session.caller_id == "race_scanner"
        # go_service_client defaults to a real, independently-scoped client.
        assert scanner._go_service_client.caller_id == "race_scanner"
        # race_parallel defaults to the real configs/scope.yaml value.
        assert scanner._race_parallel == 30
