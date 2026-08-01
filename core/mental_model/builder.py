"""
Implements: Section 3 -- core/mental_model/builder.py ("Page cap: 8
max, 10s/page, 90s total; partial on abort").
Design: mental_model_builder_prompt_design.md Section 7 ("builder.py's
own orchestration logic... is fully specified already in Section 6.3
and the Section 3 tree comment -- nothing new needed here, implement
directly from that text").
Blueprint: bb_agent_v6.6_final_blueprint.md

Orchestrates, in order: page selection + fetch (via `BrowserTool`,
Section 6.3's "homepage (always) -> login page (if linked) -> top 6
DOM-position homepage links", 8-page cap) -> per-page local-7B parse
(`flow_tracer.trace_page`, `role_mapper.extract_roles`) -> Groq call 1
(`boundary_identifier.identify_boundaries`) -> Groq call 2
(`assumption_extractor.extract_assumptions`) -> Groq call 3(+4)
(`exploitability_scorer.score_exploitability`) -> a finished
`MentalModel` with `is_partial`/`pages_analyzed`/`built_at` set.

TIMEOUTS: 10s/page (Section 6.3) is enforced per page fetch via
`asyncio.wait_for` around `BrowserTool.capture()` -- not a parameter
added to `capture()` itself, since Section 3's exact code skeleton for
`capture()` (`async def capture(self, url: str) -> BrowserCapture`)
takes no timeout argument; adding one would diverge from that cited
signature. 90s total (Section 6.3) is tracked across the whole page-
fetch phase with `time.monotonic()`; abort (stop fetching further
pages, proceed with whatever completed) the moment the budget is
exhausted, per Section 6.3: "abort -> use partial model + log
[MENTAL_MODEL_PARTIAL]" (docs/DECISIONS.md item 15).

LOGIN-LINK / TOP-6-LINK SELECTION: a bare `html.parser.HTMLParser`
subclass extracts `<a href>` targets in DOM order (no new dependency --
stdlib already provides everything needed for this narrow task; adding
BeautifulSoup/lxml for link extraction alone would be an unjustified
new dependency for something this small). "Login page" detection is a
simple href/text keyword match ("login"/"signin"/"log-in"/"sign-in") --
not cited at this level of detail anywhere in the blueprint (Section
6.3 only says "login page (if linked)"), so the exact keyword list is a
documented, narrow judgment call, not a blueprint transcription.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

from core.mental_model import assumption_extractor, boundary_identifier, exploitability_scorer
from core.mental_model.flow_tracer import trace_page
from core.ontology.mental_model import MentalModel, PageSignals

if TYPE_CHECKING:
    from core.browser.browser_tool import BrowserTool

logger = logging.getLogger(__name__)

MAX_PAGES = 8
PER_PAGE_TIMEOUT_SECONDS = 10.0
TOTAL_TIMEOUT_SECONDS = 90.0
_LOGIN_KEYWORDS = ("login", "signin", "log-in", "sign-in", "log_in", "sign_in")


class _LinkExtractor(HTMLParser):
    """Extracts `<a href>` targets in DOM order, with each link's
    visible text (for login-keyword matching against link text as well
    as href). stdlib-only -- see module docstring."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, visible_text)
        self._current_href: str | None = None
        self._current_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), None)
            if href:
                self._current_href = href
                self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            self.links.append((self._current_href, "".join(self._current_text_parts)))
            self._current_href = None
            self._current_text_parts = []


def _extract_links(html: str, base_url: str) -> list[str]:
    """Returns absolute-URL links found in `html`, in DOM order,
    resolved against `base_url`."""
    parser = _LinkExtractor()
    parser.feed(html)
    return [urllib.parse.urljoin(base_url, href) for href, _text in parser.links]


def _find_login_link(html: str, base_url: str) -> str | None:
    """Returns the first link whose href or visible text matches a
    login keyword, or `None` if none found. First match in DOM order,
    not "most likely" -- keeps this deterministic and simple, matching
    Section 6.3's own terse "if linked" (no ranking algorithm cited)."""
    parser = _LinkExtractor()
    parser.feed(html)
    for href, text in parser.links:
        haystack = f"{href} {text}".lower()
        if any(keyword in haystack for keyword in _LOGIN_KEYWORDS):
            return urllib.parse.urljoin(base_url, href)
    return None


def _select_pages(homepage_url: str, homepage_html: str) -> list[str]:
    """Section 6.3's page selection: homepage (always) -> login page
    (if linked) -> top 6 DOM-position homepage links. Deduplicated,
    capped at `MAX_PAGES` total.

    "Top 6" is read as a fixed count for that tier, not "fill whatever
    slots remain up to the cap" -- the literal phrasing names a number
    (6), and reading it as fixed is what makes the arithmetic in
    Section 6.3's own cap consistent either way: homepage(1) +
    login(0 or 1) + top-6(6) = 7 (no login found) or 8 (login found),
    never exceeding the 8-page cap on its own. A "fill to 8 regardless"
    reading would silently grab a 7th top-link whenever no login page
    exists, which is a different selection than "top 6" literally
    says.

    Args:
        homepage_url: The homepage's final URL (after any redirect).
        homepage_html: The homepage's rendered HTML, used to find both
            the login link and the top-6 links.

    Returns:
        URLs to fetch, in order: homepage first, then login page (if
        found and distinct from homepage), then up to 6 more DOM-order
        links (excluding ones already selected). Deduplication can
        still make the practical result shorter than 7/8 (e.g. if a
        homepage link happens to equal the login link already added),
        but this function never SEEKS more than 6 top-tier links to
        compensate -- capped at `MAX_PAGES` as a final, defensive
        ceiling only, not a fill target.
    """
    selected = [homepage_url]

    login_url = _find_login_link(homepage_html, homepage_url)
    if login_url and login_url not in selected:
        selected.append(login_url)

    top_links_added = 0
    for link in _extract_links(homepage_html, homepage_url):
        if top_links_added >= 6:
            break
        if link not in selected:
            selected.append(link)
            top_links_added += 1

    return selected[:MAX_PAGES]


async def build_mental_model(
    target_url: str,
    browser_tool: "BrowserTool",
    *,
    llm_config_path: Path = Path("configs/llm_config.yaml"),
    api_key: str | None = None,
) -> MentalModel:
    """Builds a complete `MentalModel` for `target_url` (Section 6.3,
    Phase 2).

    Args:
        target_url: The target application's homepage URL.
        browser_tool: A `BrowserTool` instance for scope-checked page
            fetches (Section 3's `browser_tool.py`).
        llm_config_path: Path to `configs/llm_config.yaml`, passed
            through to the three Groq-calling stages.
        api_key: Overrides the Groq keyring lookup (used by tests) --
            passed through to all three Groq-calling stages.

    Returns:
        A finished `MentalModel`: `business_purpose`/`roles`/
        `data_flows`/`trust_boundaries` (Groq call 1),
        `assumptions[].description` (Groq call 2), each assumption's
        `exploitability_score` (Groq call 3+4), plus `is_partial`,
        `pages_analyzed`, and `built_at` set by this function. If the
        8-page cap or 90s total timeout is hit before all selected
        pages are fetched, `is_partial=True` and
        `[MENTAL_MODEL_PARTIAL]` is logged (Section 6.3); the pipeline
        still runs Groq calls 1-3 on whatever pages WERE fetched, since
        a partial page set can still yield a usable (if incomplete)
        model, rather than discarding all recon effort on a timeout.

    Raises:
        core.browser.browser_tool.OutOfScopeError: If `target_url`
            itself is out of scope (raised by the homepage fetch,
            before any page-selection logic runs).
        core.mental_model._groq_client.GroqCallError: If any of the
            three Groq stages fails after retries -- this function does
            not catch or degrade Groq-stage failures, unlike per-page
            local-7B failures (`flow_tracer.py` already degrades those
            to an empty `PageSignals` on its own).
    """
    start_time = time.monotonic()
    homepage = await asyncio.wait_for(
        browser_tool.capture(target_url), timeout=PER_PAGE_TIMEOUT_SECONDS
    )
    urls_to_fetch = _select_pages(homepage.final_url, homepage.html)

    pages: list[PageSignals] = [trace_page(homepage.final_url, homepage.html, homepage.status_code)]
    is_partial = False

    for url in urls_to_fetch[1:]:
        elapsed = time.monotonic() - start_time
        if elapsed >= TOTAL_TIMEOUT_SECONDS:
            logger.warning("[MENTAL_MODEL_PARTIAL] 90s total timeout reached; %d/%d pages fetched",
                            len(pages), len(urls_to_fetch))
            is_partial = True
            break
        remaining = TOTAL_TIMEOUT_SECONDS - elapsed
        page_timeout = min(PER_PAGE_TIMEOUT_SECONDS, remaining)
        try:
            capture = await asyncio.wait_for(browser_tool.capture(url), timeout=page_timeout)
        except asyncio.TimeoutError:
            logger.warning("[MENTAL_MODEL_PARTIAL] page fetch timed out: %s", url)
            is_partial = True
            continue
        except Exception as exc:  # noqa: BLE001 -- a single page's fetch
            # failing (scope block, network error, non-2xx) degrades to
            # skipping that page, matching flow_tracer.py's own
            # per-page-failure-is-not-session-failure precedent.
            logger.warning("[MENTAL_MODEL_PAGE_PARSE_FAILED] %s: %s", url, exc)
            continue
        pages.append(trace_page(capture.final_url, capture.html, capture.status_code))

    if len(pages) < len(urls_to_fetch):
        is_partial = True

    mental_model = boundary_identifier.identify_boundaries(
        target_url, pages, llm_config_path=llm_config_path, api_key=api_key
    )
    mental_model = assumption_extractor.extract_assumptions(
        mental_model, llm_config_path=llm_config_path, api_key=api_key
    )
    mental_model = exploitability_scorer.score_exploitability(
        mental_model, llm_config_path=llm_config_path, api_key=api_key
    )

    return MentalModel(
        business_purpose=mental_model.business_purpose,
        roles=mental_model.roles,
        trust_boundaries=mental_model.trust_boundaries,
        data_flows=mental_model.data_flows,
        assumptions=mental_model.assumptions,
        is_partial=is_partial,
        pages_analyzed=len(pages),
        built_at=datetime.now(timezone.utc),
    )
