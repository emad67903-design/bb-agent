"""
Implements: test coverage for core/scanners/mass_assignment.py (Section 7.26).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.mass_assignment import (
    MassAssignmentScanner,
    _field_reflected,
    _load_payloads,
)
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="mass_assignment",
        intercepting_client=InterceptingClient(transport=transport),
    )


_ONE_PAYLOAD = [{"id": "is_admin_camel", "field_name": "isAdmin", "field_value": True}]


class TestFieldReflected:
    def test_json_exact_match(self):
        assert _field_reflected('{"username":"x","isAdmin":true}', "isAdmin", True) is True

    def test_json_whitespace_variation_still_matches(self):
        assert _field_reflected('{\n  "username": "x",\n  "isAdmin": true\n}', "isAdmin", True) is True

    def test_json_field_absent(self):
        assert _field_reflected('{"username":"x"}', "isAdmin", True) is False

    def test_json_field_present_but_different_value(self):
        assert _field_reflected('{"username":"x","isAdmin":false}', "isAdmin", True) is False

    def test_string_fallback_for_non_parseable_body(self):
        assert _field_reflected('<html>Debug: {"isAdmin": true}</html>', "isAdmin", True) is True

    def test_string_fallback_requires_quoted_key(self):
        assert _field_reflected("the isAdmin flag concept", "isAdmin", True) is False

    def test_string_valued_field(self):
        assert _field_reflected('{"role": "admin"}', "role", "admin") is True


class TestLoadPayloads:
    def test_real_payload_file_has_four_entries(self):
        payloads = _load_payloads()
        assert {p["field_name"] for p in payloads} == {"isAdmin", "is_admin", "role", "verified"}


class TestMassAssignmentScannerRegistration:
    def test_registered_under_mass_assignment(self):
        assert SCANNER_REGISTRY["mass_assignment"] is MassAssignmentScanner


class TestMassAssignmentScannerDetection:
    @pytest.mark.asyncio
    async def test_reflected_field_produces_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            if "isAdmin" in body:
                return httpx.Response(200, text=json.dumps({"username": body["username"], "isAdmin": True}))
            return httpx.Response(200, text=json.dumps({"username": body["username"]}))

        scanner = MassAssignmentScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/api/users")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "mass_assignment"
        assert candidate.parameter == "isAdmin"
        assert candidate.http_method == "POST"
        assert candidate.detected_by == "mass_assignment"
        assert candidate.probe_correlation_id is None
        assert '"isAdmin"' in candidate.payload_used

    @pytest.mark.asyncio
    async def test_field_ignored_by_server_produces_no_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            return httpx.Response(200, text=json.dumps({"username": body["username"]}))

        scanner = MassAssignmentScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/api/users")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_field_present_in_baseline_too_produces_no_candidate(self):
        """Differential requirement -- if the server ALWAYS echoes
        isAdmin:true regardless of what was sent, that's not evidence
        the injected field itself was accepted."""

        def handler(request):
            return httpx.Response(200, text=json.dumps({"isAdmin": True}))

        scanner = MassAssignmentScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/api/users")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_each_probe_uses_a_distinct_random_username(self):
        seen_usernames = []

        def handler(request):
            body = json.loads(request.content)
            seen_usernames.append(body["username"])
            return httpx.Response(200, text=json.dumps({"username": body["username"]}))

        scanner = MassAssignmentScanner(_session(handler), payloads=_ONE_PAYLOAD)
        await scanner.scan("https://example.com/api/users")
        assert len(seen_usernames) == 2  # baseline + probe
        assert seen_usernames[0] == seen_usernames[1]  # SAME username within one entry's pair
        assert seen_usernames[0].startswith("xbow_test_")


class TestMassAssignmentScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            body = json.loads(request.content)
            if "isAdmin" in body:
                return httpx.Response(200, text=json.dumps({"isAdmin": True, "filler": "K" * 2000}))
            return httpx.Response(200, text=json.dumps({"username": body["username"]}))

        scanner = MassAssignmentScanner(_session(handler), payloads=_ONE_PAYLOAD)
        candidates = await scanner.scan("https://example.com/api/users")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestMassAssignmentScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("mass_assignment", scope_domains=["example.com"])
        assert isinstance(scanner, MassAssignmentScanner)
