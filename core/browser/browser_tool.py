"""
Implements: Section 3 -- core/browser/browser_tool.py ("SCOPE
ENFORCEMENT (R-H1 fix + v6.5 route fix, V6.4-M1)").
Also implements: Section 4.4's Playwright-layer paragraph, Section 9.1's
"Playwright (Semaphore=1)" RAM budget line, and Section 10.2 item 2
("browser_tool.py (all Playwright, R-H1 fix)").
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 3 vs. WEEK 6 (docs/DECISIONS.md item 28): Section 12 lists this
component's scope check under BOTH the Week 3 row and the Week 6 row,
verbatim, with no arbitration found anywhere else in the blueprint.
Resolved by the project owner this week: Week 3 owns it, on the
strength of Section 3's own browser_tool.py comment naming
`MentalModelBuilder` as a call site, and Week 3 cannot functionally
complete without this scope check existing first. Week 6's identical
listing is treated as documented blueprint drift (the same defect class
as `v6.4-003`'s `ToolSelector` double-booking), not a second,
independent build phase.

`scope_enforcer.py` PREREQUISITE (docs/DECISIONS.md item 31): this
module's scope check literally imports and calls
`core.governance.scope_enforcer.is_allowed`. That module had no Section
12 week assignment at all (a separate, smaller gap from the Week
3-vs-6 one above); built this week as a minimal, fully
blueprint-specified (Section 4.4, verbatim) prerequisite, for the same
reason: this component cannot function without it.

`BrowserCapture` (core/ontology/browser.py) is this method's return
type -- PROVISIONAL (docs/DECISIONS.md item 32, corrected from an
earlier, inconsistent "committed" status); see that module's docstring
for the correction.

`network_observer.py` owns the actual Playwright route-handler
implementation (`page.route()`, never `page.on("request")` -- see that
module's docstring); this file registers it, per Section 3's explicit
ordering requirement: route handlers only affect requests issued after
registration, so `page.route(...)` MUST be awaited before `page.goto()`,
never after.

`Browser` lifecycle (launching/closing Playwright's Chromium instance)
is NOT this module's responsibility and is not built here -- Section 3
never assigns lifecycle ownership to `browser_tool.py` itself, and
nothing in Week 3's row requires it either. `BrowserTool` takes an
already-launched `playwright.async_api.Browser` via constructor
injection; whatever manages Playwright's actual launch/close (plausibly
`process_supervisor.py`, per Section 9.2's `PHASE_MEMORY_MODE["testing"]`
listing "playwright" as a managed process, but that module doesn't
exist yet either and isn't Week 3 scope) is a separate, later concern.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.governance.scope_enforcer import is_allowed
from core.ontology.browser import BrowserCapture
from core.browser.network_observer import build_scope_checking_route_handler

if TYPE_CHECKING:
    from playwright.async_api import Browser

logger = logging.getLogger(__name__)


class OutOfScopeError(Exception):
    """Raised by `BrowserTool.capture` when the requested URL's host is
    not in the program's scope (Section 3: `raise OutOfScopeError(f"BrowserTool
    blocked: {url}")`)."""


class BrowserTool:
    """Scope-checked Playwright page-capture entry point (Section 3).

    Every call site that needs a browser to visit a URL -- this week,
    only `MentalModelBuilder` is anywhere near ready to be one, and it
    remains blocked on the separate, larger gap in docs/DECISIONS.md's
    Week 3 completion report (no `config.py`, no established Groq/local-7B
    calling pattern anywhere yet -- `MentalModel`'s own field list is
    resolved, item 29) -- goes through `capture()`, never a
    raw `page.goto()` of its own. This is Section 10.2 item 2's "all
    Playwright" scope-enforcement layer, independent of the Python HTTP
    stack's `RateLimitedClient` layer (item 1) and the two Go services'
    own `scope_guard.go` (item 4, byte-identical, Week 0).

    Attributes:
        _browser: An already-launched Playwright `Browser` instance.
            Lifecycle (launch/close) is the caller's responsibility --
            see this module's docstring.
        _scope_domains: The program's in-scope domain patterns, loaded
            once at construction (see
            `core.governance.scope_enforcer.load_scope_domains_for_enforcement`),
            not re-read from disk on every `capture()` call.
        _semaphore: `asyncio.Semaphore(1)` -- Section 9.1 prices
            Playwright's RAM budget at concurrency=1 explicitly
            ("Playwright (Semaphore=1): 0.80 GB"); this is that
            semaphore, enforced here, not a documentation-only
            intention.
    """

    def __init__(self, browser: "Browser", scope_domains: list[str]) -> None:
        """
        Args:
            browser: An already-launched Playwright Chromium `Browser`.
            scope_domains: The program's in-scope domain patterns.
        """
        self._browser = browser
        self._scope_domains = scope_domains
        self._semaphore = asyncio.Semaphore(1)

    async def capture(self, url: str) -> BrowserCapture:
        """Scope-checks, then loads `url` in a fresh, isolated page.

        Section 3's exact sequence: scope check BEFORE anything else
        (not inside the semaphore -- an out-of-scope request should
        never even queue for a page slot); then acquire the
        concurrency-1 semaphore; then a new page, with the
        scope-checking route handler registered BEFORE `goto()` (Section
        3's `network_observer.py` comment: registering after `goto()`
        would let the initial navigation itself slip through
        unchecked).

        Args:
            url: The URL to visit.

        Returns:
            A `BrowserCapture` with the final URL (post-redirect), the
            rendered HTML, and the main navigation's HTTP status.

        Raises:
            OutOfScopeError: If `url`'s host is not in scope. Raised
                before any page is opened, any semaphore slot is
                acquired, or any network activity occurs.
        """
        if not is_allowed(url, self._scope_domains):
            raise OutOfScopeError(f"BrowserTool blocked: {url}")

        async with self._semaphore:
            page = await self._browser.new_page()
            try:
                handler = build_scope_checking_route_handler(self._scope_domains)
                await page.route("**/*", handler)  # BEFORE goto() -- see module docstring.
                response = await page.goto(url)
                html = await page.content()
                return BrowserCapture(
                    requested_url=url,
                    final_url=page.url,
                    html=html,
                    status_code=response.status if response is not None else None,
                )
            finally:
                await page.close()
