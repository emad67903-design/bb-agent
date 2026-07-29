"""
Implements: Section 3 / Section 4.4 / Section 9.1 test coverage --
browser_tool.py

Runs against a REAL local HTTP server and a REAL headless Chromium
(Playwright), not mocks -- this project's own standard is programmatic
verification over eyeballing, and the scope check, redirect handling,
sub-resource aborting, and concurrency limit are exactly the kind of
thing a mock could accidentally assert past. The local server binds
127.0.0.1 on an OS-assigned port; no network egress, no real external
host is contacted.
"""

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from core.browser.browser_tool import BrowserTool, OutOfScopeError
from core.browser.network_observer import build_scope_checking_route_handler


class _Handler(BaseHTTPRequestHandler):
    """Minimal test server: tracks every path it was asked for, and
    serves a handful of fixed routes used by the tests below."""

    request_log: list[str] = []
    slow_delay_seconds = 0.0

    def log_message(self, *args) -> None:  # noqa: D102 -- silence default stderr logging
        pass

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own naming
        _Handler.request_log.append(self.path)

        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if self.path == "/slow":
            time.sleep(_Handler.slow_delay_seconds)
            self._send_html("<html><body>slow page</body></html>")
            return

        if self.path == "/with-subresources":
            self._send_html(
                '<html><body>'
                '<img src="/in-scope.png" id="in">'
                '<img src="http://out-of-scope.test/blocked.png" id="out">'
                "</body></html>"
            )
            return

        if self.path == "/in-scope.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"\x89PNG\r\n")
            return

        self._send_html("<html><body>hello from test server</body></html>")

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture
def test_server():
    _Handler.request_log = []
    _Handler.slow_delay_seconds = 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}", _Handler
    server.shutdown()
    thread.join(timeout=5)


def _run(coro):
    return asyncio.run(coro)


async def _make_browser_tool(scope_domains: list[str]):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    return playwright, browser, BrowserTool(browser, scope_domains)


class TestCaptureScopeEnforcement:
    def test_out_of_scope_url_raises_before_any_request(self, test_server):
        base_url, handler = test_server

        async def scenario():
            playwright, browser, tool = await _make_browser_tool(["example.com"])
            try:
                with pytest.raises(OutOfScopeError, match="BrowserTool blocked"):
                    await tool.capture(base_url + "/")
            finally:
                await browser.close()
                await playwright.stop()

        _run(scenario())
        assert handler.request_log == [], (
            "an out-of-scope capture() must never reach the server at all"
        )

    def test_in_scope_url_succeeds_and_returns_html(self, test_server):
        base_url, _handler = test_server

        async def scenario():
            playwright, browser, tool = await _make_browser_tool(["127.0.0.1"])
            try:
                return await tool.capture(base_url + "/")
            finally:
                await browser.close()
                await playwright.stop()

        result = _run(scenario())
        assert result.requested_url == base_url + "/"
        assert result.final_url == base_url + "/"
        assert result.status_code == 200
        assert "hello from test server" in result.html

    def test_redirect_updates_final_url(self, test_server):
        base_url, _handler = test_server

        async def scenario():
            playwright, browser, tool = await _make_browser_tool(["127.0.0.1"])
            try:
                return await tool.capture(base_url + "/redirect")
            finally:
                await browser.close()
                await playwright.stop()

        result = _run(scenario())
        assert result.requested_url == base_url + "/redirect"
        assert result.final_url == base_url + "/"
        assert "hello from test server" in result.html


class TestSubRequestAborting:
    """network_observer.py's route handler, exercised end-to-end through
    a real page load rather than a fake Route (test_network_observer.py
    covers the handler's decision logic in isolation; this proves it
    also works wired into an actual Playwright page)."""

    def test_out_of_scope_subresource_is_aborted_in_scope_one_is_not(self, test_server):
        base_url, _handler = test_server
        failed_urls: list[str] = []
        finished_urls: list[str] = []

        async def scenario():
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                page.on("requestfailed", lambda req: failed_urls.append(req.url))
                page.on("requestfinished", lambda req: finished_urls.append(req.url))
                # Same handler BrowserTool.capture() registers internally
                # (Section 3: page.route(), registered before goto()).
                handler = build_scope_checking_route_handler(["127.0.0.1"])
                await page.route("**/*", handler)
                await page.goto(base_url + "/with-subresources")
                await page.wait_for_timeout(300)
                await page.close()
            finally:
                await browser.close()
                await playwright.stop()

        _run(scenario())

        assert any("out-of-scope.test/blocked.png" in u for u in failed_urls), (
            f"expected the out-of-scope image request to be aborted; failed_urls={failed_urls}"
        )
        assert any("/in-scope.png" in u for u in finished_urls), (
            f"expected the in-scope image request to finish; finished_urls={finished_urls}"
        )
        assert not any("out-of-scope.test" in u for u in finished_urls), (
            "the out-of-scope request must never finish successfully"
        )


class TestConcurrencyLimit:
    """Section 9.1 prices Playwright at Semaphore(1) explicitly -- this
    proves captures are serialized, not just documented as such."""

    def test_concurrent_captures_are_serialized_not_parallel(self, test_server):
        base_url, handler = test_server
        handler.slow_delay_seconds = 0.2

        async def scenario():
            playwright, browser, tool = await _make_browser_tool(["127.0.0.1"])
            try:
                start = time.monotonic()
                await asyncio.gather(
                    tool.capture(base_url + "/slow"),
                    tool.capture(base_url + "/slow"),
                    tool.capture(base_url + "/slow"),
                )
                return time.monotonic() - start
            finally:
                await browser.close()
                await playwright.stop()

        elapsed = _run(scenario())
        # Fully parallel would take ~0.2s; fully serialized (Semaphore(1))
        # takes ~0.6s. 0.5s threshold gives headroom for CI jitter while
        # still clearly separating the two outcomes.
        assert elapsed >= 0.5, (
            f"three /slow captures completed in {elapsed:.2f}s -- looks parallel, "
            "not serialized through Semaphore(1)"
        )
