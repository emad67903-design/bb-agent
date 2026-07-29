"""
Implements: Section 3 / Section 4.4 test coverage -- network_observer.py
Blueprint: bb_agent_v6.6_final_blueprint.md

Uses a minimal fake Route (not a real Playwright browser) so this suite
is fast and has no browser-binary dependency -- browser_tool.py's own
test module covers the real, end-to-end Playwright behavior.
"""

import asyncio

import pytest

from core.browser.network_observer import build_scope_checking_route_handler


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    """Records which of abort()/continue_() was called, mirroring the
    two methods Section 3 requires (`.abort()`/`.continue_()`) without
    needing a real Playwright `Route`."""

    def __init__(self, url: str) -> None:
        self.request = FakeRequest(url)
        self.aborted = False
        self.continued = False

    async def abort(self) -> None:
        self.aborted = True

    async def continue_(self) -> None:
        self.continued = True


def _run(coro):
    return asyncio.run(coro)


class TestBuildScopeCheckingRouteHandler:
    def test_in_scope_request_continues(self):
        handler = build_scope_checking_route_handler(["*.example.com"])
        route = FakeRoute("https://api.example.com/data.json")
        _run(handler(route))
        assert route.continued is True
        assert route.aborted is False

    def test_out_of_scope_request_aborts(self):
        handler = build_scope_checking_route_handler(["*.example.com"])
        route = FakeRoute("https://evil.test/tracker.js")
        _run(handler(route))
        assert route.aborted is True
        assert route.continued is False

    def test_exact_domain_match_continues(self):
        handler = build_scope_checking_route_handler(["example.com"])
        route = FakeRoute("https://example.com/")
        _run(handler(route))
        assert route.continued is True

    def test_handler_is_reusable_across_multiple_requests(self):
        """The same handler instance must be safely callable for every
        sub-request a page issues -- it's registered once per
        `page.route()` call, not rebuilt per request."""
        handler = build_scope_checking_route_handler(["*.example.com"])
        in_scope = FakeRoute("https://cdn.example.com/style.css")
        out_of_scope = FakeRoute("https://tracker.evil.test/pixel.gif")
        _run(handler(in_scope))
        _run(handler(out_of_scope))
        assert in_scope.continued is True
        assert out_of_scope.aborted is True
