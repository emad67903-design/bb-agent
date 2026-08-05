"""
Implements: Section 10.5 test coverage -- core/http/intercepting_client.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from core.http.intercepting_client import (
    BODY_CAP_BYTES,
    ENTRY_CAP,
    METADATA_SUPPRESS_PATHS,
    WARN_THRESHOLD,
    InMemoryTrafficLogStore,
    InterceptingClient,
    _should_suppress_body,
)
from core.ontology.http import TrafficEntry


class TestShouldSuppressBody:
    def test_direct_metadata_path_suppressed(self):
        assert _should_suppress_body("/latest/meta-data/ami-id", "http://169.254.169.254/latest/meta-data/ami-id") is True

    def test_unrelated_path_not_suppressed(self):
        assert _should_suppress_body("/api/users", "http://target/api/users") is False

    def test_metadata_url_in_query_param_suppressed(self):
        url = "http://target/api/fetch?url=http://169.254.169.254/latest/meta-data/ami-id"
        assert _should_suppress_body("/api/fetch", url) is True

    def test_metadata_url_percent_encoded_in_query_param_suppressed(self):
        url = "http://target/api/fetch?url=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data%2Fami-id"
        assert _should_suppress_body("/api/fetch", url) is True

    def test_unrelated_query_param_not_suppressed(self):
        url = "http://target/api/fetch?url=http://example.com/page"
        assert _should_suppress_body("/api/fetch", url) is False

    # --- item 52: body-based suppression ---

    def test_form_urlencoded_percent_encoded_body_suppressed(self):
        """The exact gap the advisor caught: a raw substring scan alone
        misses this because the payload is still percent-encoded."""
        body = "url=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data%2Fami-id"
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="application/x-www-form-urlencoded",
        ) is True

    def test_raw_substring_scan_alone_would_miss_the_percent_encoded_body(self):
        """Regression pin: confirms *why* the parse_qs() branch is
        necessary, not just that the function happens to work."""
        body = "url=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data%2Fami-id"
        assert "/latest/meta-data/" not in body

    def test_form_urlencoded_with_charset_suffix_still_matches(self):
        """Content-Type commonly carries a charset parameter -- substring
        match on content_type, not equality, so this must still work."""
        body = "url=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data%2Fami-id"
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        ) is True

    def test_content_type_matching_is_case_insensitive(self):
        """The advisor's specific follow-up catch: a real, RFC-valid
        request can use different casing for the Content-Type value."""
        body = "url=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data%2Fami-id"
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="Application/X-WWW-Form-Urlencoded",
        ) is True

    def test_content_type_case_insensitive_with_charset_and_mixed_case(self):
        body = "url=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data%2Fami-id"
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="APPLICATION/X-WWW-FORM-URLENCODED; Charset=utf-8",
        ) is True

    def test_json_body_suppressed_via_raw_substring_scan(self):
        body = '{"target_url": "http://169.254.169.254/latest/meta-data/ami-id"}'
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="application/json",
        ) is True

    def test_nested_json_body_suppressed_without_recursive_parsing(self):
        body = '{"data": {"target": {"url": "http://169.254.169.254/latest/meta-data/ami-id"}}}'
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="application/json",
        ) is True

    def test_unrecognized_content_type_falls_back_to_substring_scan(self):
        body = "some blob containing http://169.254.169.254/latest/meta-data/ami-id inline"
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="application/octet-stream",
        ) is True

    def test_absent_content_type_falls_back_to_substring_scan(self):
        body = "http://169.254.169.254/latest/meta-data/ami-id"
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body, content_type=None,
        ) is True

    def test_bytes_body_decoded_before_scanning(self):
        body = b'{"url": "http://169.254.169.254/latest/meta-data/ami-id"}'
        assert _should_suppress_body(
            "/api/fetch", "http://target/api/fetch", request_body=body,
            content_type="application/json",
        ) is True

    def test_unrelated_form_body_not_suppressed(self):
        body = "username=alice&password=hunter2"
        assert _should_suppress_body(
            "/login", "http://target/login", request_body=body,
            content_type="application/x-www-form-urlencoded",
        ) is False

    def test_unrelated_json_body_not_suppressed(self):
        body = '{"username": "alice"}'
        assert _should_suppress_body(
            "/login", "http://target/login", request_body=body,
            content_type="application/json",
        ) is False

    def test_none_body_not_suppressed_by_body_branch(self):
        assert _should_suppress_body("/api/users", "http://target/api/users", request_body=None) is False

    def test_empty_body_not_suppressed_by_body_branch(self):
        assert _should_suppress_body("/api/users", "http://target/api/users", request_body="") is False


class TestInMemoryTrafficLogStore:
    def _entry(self, url: str = "http://x/y") -> TrafficEntry:
        from datetime import datetime, timezone
        return TrafficEntry(
            method="GET", url=url, status_code=200, headers={},
            body_preview="ok", body_sha256=None, suppressed=False,
            timestamp=datetime.now(timezone.utc),
        )

    def test_logs_and_counts_entries(self):
        store = InMemoryTrafficLogStore()
        store.log_entry(self._entry())
        store.log_entry(self._entry())
        assert store.entry_count == 2

    def test_evicts_oldest_once_entry_cap_reached(self):
        store = InMemoryTrafficLogStore(max_entries=5)
        for i in range(8):
            store.log_entry(self._entry(url=f"http://x/{i}"))
        assert store.entry_count == 5
        # oldest 3 (0..2) evicted; entry 3 should be the oldest remaining
        assert store.entries[0].url == "http://x/3"
        assert store.entries[-1].url == "http://x/7"

    def test_default_max_entries_is_entry_cap(self):
        store = InMemoryTrafficLogStore()
        assert store.max_entries == ENTRY_CAP


class TestInterceptingClient:
    def _client(self, handler) -> InterceptingClient:
        return InterceptingClient(transport=httpx.MockTransport(handler))

    @pytest.mark.asyncio
    async def test_returns_the_real_response_unmodified(self):
        client = self._client(lambda request: httpx.Response(200, json={"ok": True}))
        response = await client.request("GET", "http://target/safe")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        await client.aclose()

    @pytest.mark.asyncio
    async def test_logs_one_entry_per_request(self):
        client = self._client(lambda request: httpx.Response(200, text="hello"))
        await client.request("GET", "http://target/a")
        await client.request("GET", "http://target/b")
        assert client.store.entry_count == 2
        await client.aclose()

    @pytest.mark.asyncio
    async def test_small_body_stored_as_preview_not_hash(self):
        client = self._client(lambda request: httpx.Response(200, text="small body"))
        await client.request("GET", "http://target/a")
        entry = client.store.entries[0]
        assert entry.body_preview == "small body"
        assert entry.body_sha256 is None
        assert entry.suppressed is False
        await client.aclose()

    @pytest.mark.asyncio
    async def test_suppressed_response_logs_no_body_at_all(self):
        client = self._client(lambda request: httpx.Response(200, text="secret AWS creds"))
        await client.request("GET", "http://169.254.169.254/latest/meta-data/ami-id")
        entry = client.store.entries[0]
        assert entry.suppressed is True
        assert entry.body_preview is None
        assert entry.body_sha256 is None
        # status code and headers ARE still logged (Section 10.5)
        assert entry.status_code == 200
        await client.aclose()

    @pytest.mark.asyncio
    async def test_large_non_suppressed_body_hashed_not_stored_inline(self, tmp_path):
        large_body = "x" * (BODY_CAP_BYTES + 1)
        client = InterceptingClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=large_body)),
            telemetry_dir=tmp_path,
        )
        await client.request("GET", "http://target/big")
        entry = client.store.entries[0]
        assert entry.body_preview is None
        assert entry.body_sha256 == hashlib.sha256(large_body.encode()).hexdigest()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_large_body_written_to_telemetry_dir(self, tmp_path):
        large_body = "y" * (BODY_CAP_BYTES + 100)
        client = InterceptingClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=large_body)),
            telemetry_dir=tmp_path,
        )
        await client.request("GET", "http://target/big")
        entry = client.store.entries[0]
        written = (tmp_path / entry.body_sha256).read_text()
        assert written == large_body
        await client.aclose()

    @pytest.mark.asyncio
    async def test_body_exactly_at_cap_is_preview_not_hashed(self, tmp_path):
        """Section 10.5 says '> 8 KB' triggers offload -- exactly at the
        cap should still be an inline preview."""
        body_at_cap = "z" * BODY_CAP_BYTES
        client = InterceptingClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body_at_cap)),
            telemetry_dir=tmp_path,
        )
        await client.request("GET", "http://target/exact")
        entry = client.store.entries[0]
        assert entry.body_preview == body_at_cap
        assert entry.body_sha256 is None
        await client.aclose()

    @pytest.mark.asyncio
    async def test_post_body_and_content_type_passed_through_to_suppression_check(self):
        client = self._client(lambda request: httpx.Response(200, text="creds here"))
        await client.request(
            "POST", "http://target/api/fetch",
            headers={"Content-Type": "application/json"},
            content='{"url": "http://169.254.169.254/latest/meta-data/ami-id"}',
        )
        entry = client.store.entries[0]
        assert entry.suppressed is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_overflow_warning_logged_once_when_threshold_crossed(self, caplog):
        """Uses a minimal fake store reporting a fixed entry_count,
        rather than populating InMemoryTrafficLogStore with tens of
        thousands of real entries -- tests the same `_check_overflow`
        comparison (`entry_count >= WARN_THRESHOLD * ENTRY_CAP`) without
        needing real bulk data to reach it."""
        threshold = int(WARN_THRESHOLD * ENTRY_CAP)
        store = _FixedCountStore(count=threshold - 1)
        client = InterceptingClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")), store=store)

        with caplog.at_level("WARNING", logger="core.http.intercepting_client"):
            store.count = threshold  # simulate crossing the threshold
            await client.request("GET", "http://target/a")  # triggers _check_overflow
            await client.request("GET", "http://target/b")  # stays at/over threshold

        overflow_logs = [r for r in caplog.records if "INTERCEPT_OVERFLOW" in r.message]
        assert len(overflow_logs) == 1  # logged once, not on every subsequent entry
        await client.aclose()

    @pytest.mark.asyncio
    async def test_no_overflow_warning_below_threshold(self, caplog):
        store = _FixedCountStore(count=int(WARN_THRESHOLD * ENTRY_CAP) - 2)
        client = InterceptingClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")), store=store)
        with caplog.at_level("WARNING", logger="core.http.intercepting_client"):
            await client.request("GET", "http://target/a")
        assert not any("INTERCEPT_OVERFLOW" in r.message for r in caplog.records)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_async_context_manager_closes_client(self):
        async with self._client(lambda request: httpx.Response(200, text="ok")) as client:
            await client.request("GET", "http://target/a")
        assert client.store.entry_count == 1


class _FixedCountStore:
    """Minimal `TrafficLogStore`: accepts entries (discarding them) and
    reports whatever `entry_count` its `count` attribute is set to.
    Used only to test `InterceptingClient._check_overflow`'s threshold
    comparison in isolation, without needing real bulk data."""

    def __init__(self, count: int) -> None:
        self.count = count

    def log_entry(self, entry: TrafficEntry) -> None:
        pass

    @property
    def entry_count(self) -> int:
        return self.count
