"""
Implements: Section 3 -- core/browser/network_observer.py ("v6.5 fix
(V6.4-M1): page.on(\"request\") is a read-only event listener in
Playwright -- it cannot abort a request. Only page.route() returns a
Route object with .abort()/.continue_()"). Also implements Section 4.4's
Playwright-layer paragraph and Section 10.2 item 2 (scope enforced at
every `browser_tool.py` call site).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 3 SCOPE: `browser_tool.py`'s own Section 3 comment block and this
file's Section 3 comment block describe the *identical* mechanism
(`page.route("**/*", handle_route)`, registered before `goto()`,
`abort()`/`continue_()` based on `scope_enforcer.is_allowed()`). Rather
than each file re-implementing the same route-abort logic independently
-- which would recreate exactly the kind of duplication-and-drift risk
the Engineering Constitution's "ONE HTTP LAYER, NO EXCEPTIONS" rule
exists to prevent for the raw-HTTP side -- this module owns the one
route-handler implementation, and `browser_tool.py` imports it. Section
3's own note that "this check applies to EVERY browser_tool.py call
site: MentalModelBuilder, xss_verifier.py, csrf_scanner.py,
visual_diff.py, interaction_recorder.py" supports a single shared
implementation over five independent copies -- only the first of those
five callers (via `MentalModelBuilder`, itself still blocked -- on the
larger Groq/local-7B-calling-infrastructure gap flagged in the Week 3
completion report, not on `MentalModel`'s field list, which is now
resolved, item 29) is anywhere near Week 3 scope; the other four are
Week 7+ scanners/verifiers. This module does not import or reference
any of them.

Registration timing matters and is enforced by the caller, not this
module: `page.route()` must be called immediately after `new_page()`
and before `goto()` -- route handlers only affect requests issued after
registration, so registering after `goto()` would let the initial
navigation itself slip through unchecked (this file's own blueprint
comment, verbatim). `browser_tool.py`'s `capture()` is responsible for
that ordering; this module only builds the handler function itself.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from core.governance.scope_enforcer import is_allowed

if TYPE_CHECKING:
    from playwright.async_api import Route

logger = logging.getLogger(__name__)

RouteHandler = Callable[["Route"], Awaitable[None]]


def build_scope_checking_route_handler(scope_domains: list[str]) -> RouteHandler:
    """Builds a Playwright route handler that aborts out-of-scope
    sub-requests and allows in-scope ones.

    Section 3 (this file) + Section 4.4: the ONLY correct mechanism is
    `page.route()`, never `page.on("request")` (a read-only listener
    that cannot call `.abort()`). The returned handler is meant to be
    registered via `await page.route("**/*", handler)` immediately
    after `new_page()` and before any `goto()` call.

    Args:
        scope_domains: The program's in-scope domain patterns (see
            `core.governance.scope_enforcer._is_scope_allowed`),
            captured once when the handler is built rather than
            re-loaded from disk on every intercepted sub-request.

    Returns:
        An async callable suitable for `page.route("**/*", handler)`:
        awaits `route.abort()` for an out-of-scope request URL, or
        `route.continue_()` for an in-scope one.
    """

    async def handle_route(route: "Route") -> None:
        request_url = route.request.url
        if is_allowed(request_url, scope_domains):
            await route.continue_()
        else:
            logger.info("network_observer: aborted out-of-scope sub-request %s", request_url)
            await route.abort()

    return handle_route
