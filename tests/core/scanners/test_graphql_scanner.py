"""
Implements: test coverage for core/scanners/graphql_scanner.py (Section 7.23).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.graphql_scanner import GraphQLScanner, _find_id_query_field, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="graphql_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


_SCHEMA_WITH_ID_FIELD = {
    "data": {
        "__schema": {
            "queryType": {"name": "Query"},
            "types": [
                {
                    "name": "Query",
                    "fields": [
                        {"name": "viewer", "args": []},
                        {"name": "user", "args": [{"name": "id"}]},
                    ],
                }
            ],
        }
    }
}
_SCHEMA_NO_ID_FIELD = {
    "data": {
        "__schema": {
            "queryType": {"name": "Query"},
            "types": [{"name": "Query", "fields": [{"name": "viewer", "args": []}]}],
        }
    }
}
_INTROSPECTION_DISABLED = {"errors": [{"message": "introspection is disabled"}]}
_BATCHING_ONLY = [{"id": "batch_size_probe", "technique": "batching", "batch_size": 5}]


class TestFindIdQueryField:
    def test_finds_field_with_id_argument(self):
        assert _find_id_query_field(_SCHEMA_WITH_ID_FIELD) == "user"

    def test_no_matching_field_returns_none(self):
        assert _find_id_query_field(_SCHEMA_NO_ID_FIELD) is None

    def test_introspection_disabled_returns_none(self):
        assert _find_id_query_field(_INTROSPECTION_DISABLED) is None

    def test_non_dict_input_returns_none(self):
        assert _find_id_query_field("not a dict") is None


class TestLoadPayloads:
    def test_real_payload_file_loads(self):
        payloads = _load_payloads()
        assert len(payloads) == 1
        assert payloads[0]["technique"] == "batching"
        assert payloads[0]["batch_size"] == 25


class TestGraphQLScannerRegistration:
    def test_registered_under_graphql_scanner(self):
        assert SCANNER_REGISTRY["graphql_scanner"] is GraphQLScanner


class TestGraphQLScannerIntrospection:
    @pytest.mark.asyncio
    async def test_introspection_enabled_produces_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            if "__schema" in body["query"]:
                return httpx.Response(200, text=json.dumps(_SCHEMA_NO_ID_FIELD))
            return httpx.Response(200, text=json.dumps({"data": {}}))

        scanner = GraphQLScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/graphql")

        introspection_candidates = [c for c in candidates if c.parameter is None and "__schema" in c.payload_used]
        assert len(introspection_candidates) == 1
        candidate = introspection_candidates[0]
        assert candidate.vuln_type == "graphql"
        assert candidate.http_method == "POST"
        assert candidate.detected_by == "graphql_scanner"
        assert candidate.probe_correlation_id is None

    @pytest.mark.asyncio
    async def test_introspection_disabled_produces_no_introspection_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            if "__schema" in body["query"]:
                return httpx.Response(200, text=json.dumps(_INTROSPECTION_DISABLED))
            return httpx.Response(200, text=json.dumps({"data": {}}))

        scanner = GraphQLScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/graphql")
        assert candidates == []


class TestGraphQLScannerIdor:
    @pytest.mark.asyncio
    async def test_id_field_returning_data_produces_idor_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            query = body["query"]
            if "__schema" in query:
                return httpx.Response(200, text=json.dumps(_SCHEMA_WITH_ID_FIELD))
            if "user(id:" in query:
                return httpx.Response(200, text=json.dumps({"data": {"user": {"__typename": "User"}}}))
            return httpx.Response(200, text=json.dumps({"data": {}}))

        scanner = GraphQLScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/graphql")

        idor_candidates = [c for c in candidates if c.parameter == "user"]
        assert len(idor_candidates) == 1
        assert idor_candidates[0].http_method == "POST"

    @pytest.mark.asyncio
    async def test_id_field_denying_access_produces_no_idor_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            query = body["query"]
            if "__schema" in query:
                return httpx.Response(200, text=json.dumps(_SCHEMA_WITH_ID_FIELD))
            if "user(id:" in query:
                return httpx.Response(200, text=json.dumps({"data": {"user": None}, "errors": [{"message": "denied"}]}))
            return httpx.Response(200, text=json.dumps({"data": {}}))

        scanner = GraphQLScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/graphql")
        idor_candidates = [c for c in candidates if c.parameter == "user"]
        assert idor_candidates == []

    @pytest.mark.asyncio
    async def test_no_id_field_in_schema_skips_idor_probe_entirely(self):
        queries_sent = []

        def handler(request):
            body = json.loads(request.content)
            queries_sent.append(body["query"])
            if "__schema" in body["query"]:
                return httpx.Response(200, text=json.dumps(_SCHEMA_NO_ID_FIELD))
            return httpx.Response(200, text=json.dumps({"data": {}}))

        scanner = GraphQLScanner(_session(handler), payloads=[])
        await scanner.scan("https://example.com/graphql")
        assert not any("id:" in q for q in queries_sent)


class TestGraphQLScannerBatching:
    @pytest.mark.asyncio
    async def test_full_batch_processed_produces_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            if isinstance(body, list):
                return httpx.Response(200, text=json.dumps([{"data": {"__typename": "Query"}}] * len(body)))
            return httpx.Response(200, text=json.dumps(_INTROSPECTION_DISABLED))

        scanner = GraphQLScanner(_session(handler), payloads=_BATCHING_ONLY)
        candidates = await scanner.scan("https://example.com/graphql")
        batching_candidates = [c for c in candidates if "batch of 5" in c.payload_used]
        assert len(batching_candidates) == 1
        assert batching_candidates[0].parameter is None

    @pytest.mark.asyncio
    async def test_batch_rejected_produces_no_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            if isinstance(body, list):
                return httpx.Response(400, text=json.dumps({"errors": [{"message": "batching not allowed"}]}))
            return httpx.Response(200, text=json.dumps(_INTROSPECTION_DISABLED))

        scanner = GraphQLScanner(_session(handler), payloads=_BATCHING_ONLY)
        candidates = await scanner.scan("https://example.com/graphql")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_batch_truncated_produces_no_candidate(self):
        def handler(request):
            body = json.loads(request.content)
            if isinstance(body, list):
                return httpx.Response(200, text=json.dumps([{"data": {}}] * 2))  # fewer than requested
            return httpx.Response(200, text=json.dumps(_INTROSPECTION_DISABLED))

        scanner = GraphQLScanner(_session(handler), payloads=_BATCHING_ONLY)
        candidates = await scanner.scan("https://example.com/graphql")
        assert candidates == []


class TestGraphQLScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        def handler(request):
            body = json.loads(request.content)
            if isinstance(body, list):
                return httpx.Response(200, text=json.dumps([{"data": {}, "filler": "N" * 2000}] * len(body)))
            return httpx.Response(200, text=json.dumps(_INTROSPECTION_DISABLED))

        scanner = GraphQLScanner(_session(handler), payloads=_BATCHING_ONLY)
        candidates = await scanner.scan("https://example.com/graphql")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestGraphQLScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("graphql_scanner", scope_domains=["example.com"])
        assert isinstance(scanner, GraphQLScanner)
