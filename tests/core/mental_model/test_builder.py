"""
Implements: Section 6.3 test coverage -- core/mental_model/builder.py

Found missing during the Week 3 final-verification pass (this file did
not exist before that check) -- see docs/DECISIONS.md's Week 3
completion report for the honest record of that gap.

All dependencies (BrowserTool, flow_tracer, the three Groq-calling
stages) are mocked/faked -- this suite tests builder.py's own
orchestration logic (page selection, timeouts, partial-abort handling),
not the already-independently-tested internals of any of those.
"""

import asyncio

import pytest

from core.mental_model import builder
from core.mental_model.builder import (
    MAX_PAGES,
    _extract_links,
    _find_login_link,
    _select_pages,
    build_mental_model,
)
from core.ontology.mental_model import Assumption, MentalModel, PageSignals


# --- Pure-function tests: link extraction / page selection --------------


class TestExtractLinks:
    def test_extracts_links_in_dom_order(self):
        html = '<a href="/b">B</a><a href="/a">A</a><a href="/c">C</a>'
        assert _extract_links(html, "https://target.test/") == [
            "https://target.test/b",
            "https://target.test/a",
            "https://target.test/c",
        ]

    def test_resolves_relative_urls_against_base(self):
        html = '<a href="page.html">P</a>'
        assert _extract_links(html, "https://target.test/dir/") == [
            "https://target.test/dir/page.html"
        ]

    def test_ignores_anchors_without_href(self):
        html = '<a name="anchor">no href</a><a href="/real">real</a>'
        assert _extract_links(html, "https://target.test/") == ["https://target.test/real"]

    def test_no_links_returns_empty_list(self):
        assert _extract_links("<html><body>no links here</body></html>", "https://target.test/") == []


class TestFindLoginLink:
    def test_finds_link_by_href_keyword(self):
        html = '<a href="/account/login">Sign in here</a>'
        assert _find_login_link(html, "https://target.test/") == "https://target.test/account/login"

    def test_finds_link_by_visible_text_keyword(self):
        html = '<a href="/auth/start">Login</a>'
        assert _find_login_link(html, "https://target.test/") == "https://target.test/auth/start"

    def test_returns_none_when_no_login_link_present(self):
        html = '<a href="/about">About</a><a href="/contact">Contact</a>'
        assert _find_login_link(html, "https://target.test/") is None

    def test_first_match_wins_when_multiple_present(self):
        html = '<a href="/signin">Sign In</a><a href="/login2">Login Again</a>'
        assert _find_login_link(html, "https://target.test/") == "https://target.test/signin"


class TestSelectPages:
    def test_homepage_always_first(self):
        pages = _select_pages("https://target.test/", "<html></html>")
        assert pages[0] == "https://target.test/"

    def test_login_link_included_second_when_present(self):
        html = '<a href="/login">Login</a><a href="/about">About</a>'
        pages = _select_pages("https://target.test/", html)
        assert pages[1] == "https://target.test/login"

    def test_top_6_dom_links_follow(self):
        links = "".join(f'<a href="/page{i}">P{i}</a>' for i in range(10))
        pages = _select_pages("https://target.test/", links)
        # No login link present: homepage + top 6 = 7 total.
        assert len(pages) == 7
        assert pages[1:] == [f"https://target.test/page{i}" for i in range(6)]

    def test_capped_at_max_pages(self):
        html = '<a href="/login">Login</a>' + "".join(
            f'<a href="/page{i}">P{i}</a>' for i in range(10)
        )
        pages = _select_pages("https://target.test/", html)
        assert len(pages) == MAX_PAGES

    def test_deduplicates_repeated_links(self):
        html = '<a href="/x">X</a><a href="/x">X again</a><a href="/y">Y</a>'
        pages = _select_pages("https://target.test/", html)
        assert pages.count("https://target.test/x") == 1

    def test_login_link_not_duplicated_in_top_6(self):
        html = '<a href="/login">Login</a>' + "".join(
            f'<a href="/page{i}">P{i}</a>' for i in range(3)
        ) + '<a href="/login">Login again</a>'
        pages = _select_pages("https://target.test/", html)
        assert pages.count("https://target.test/login") == 1


# --- Orchestration tests: build_mental_model -----------------------------


class _FakeCapture:
    def __init__(self, final_url: str, html: str, status_code: int = 200):
        self.final_url = final_url
        self.html = html
        self.status_code = status_code


class _FakeBrowserTool:
    """Duck-types BrowserTool's async capture(url) -> BrowserCapture
    interface -- no real Playwright/Browser needed to test builder.py's
    own orchestration.

    `delays` is per-URL (not a single blanket delay) specifically so a
    test can slow down one sub-page without also slowing the homepage
    fetch, which uses the same PER_PAGE_TIMEOUT_SECONDS budget but is
    NOT wrapped in the per-page try/except (builder.py's own docstring:
    a homepage that can't be fetched at all leaves nothing to build a
    MentalModel from, unlike an optional secondary page).
    """

    def __init__(
        self,
        pages: dict[str, "_FakeCapture | Exception"],
        delays: dict[str, float] | None = None,
    ):
        self._pages = pages
        self._delays = delays or {}
        self.calls: list[str] = []

    async def capture(self, url: str):
        self.calls.append(url)
        delay = self._delays.get(url, 0.0)
        if delay:
            await asyncio.sleep(delay)
        result = self._pages.get(url, _FakeCapture(url, "<html></html>"))
        if isinstance(result, Exception):
            raise result
        return result


def _empty_page_signals(final_url: str, html: str = "", status_code: int | None = 200) -> PageSignals:
    return PageSignals(page_url=final_url)


def _mock_groq_pipeline(mocker):
    """Patches the three Groq-calling stages to a fast, deterministic
    happy path, so orchestration tests don't also exercise (already
    separately tested) Groq-call internals."""
    mocker.patch(
        "core.mental_model.builder.boundary_identifier.identify_boundaries",
        return_value=MentalModel(business_purpose="p", roles=["r"]),
    )
    mocker.patch(
        "core.mental_model.builder.assumption_extractor.extract_assumptions",
        side_effect=lambda mm, **kw: MentalModel(
            business_purpose=mm.business_purpose,
            roles=mm.roles,
            assumptions=[Assumption(description="a", exploitability_score=0.0)],
        ),
    )
    mocker.patch(
        "core.mental_model.builder.exploitability_scorer.score_exploitability",
        side_effect=lambda mm, **kw: MentalModel(
            business_purpose=mm.business_purpose,
            roles=mm.roles,
            assumptions=[Assumption(description="a", exploitability_score=0.6)],
        ),
    )


class TestBuildMentalModelHappyPath:
    def test_full_pipeline_produces_scored_mental_model(self, mocker):
        _mock_groq_pipeline(mocker)
        mocker.patch("core.mental_model.builder.trace_page", side_effect=_empty_page_signals)
        tool = _FakeBrowserTool({"https://target.test/": _FakeCapture("https://target.test/", "<html></html>")})

        result = asyncio.run(build_mental_model("https://target.test/", tool))

        assert result.business_purpose == "p"
        assert result.assumptions[0].exploitability_score == 0.6
        assert result.is_partial is False
        assert result.pages_analyzed == 1
        assert result.built_at is not None

    def test_fetches_homepage_plus_discovered_links(self, mocker):
        _mock_groq_pipeline(mocker)
        mocker.patch("core.mental_model.builder.trace_page", side_effect=_empty_page_signals)
        homepage_html = '<a href="/login">Login</a><a href="/about">About</a>'
        tool = _FakeBrowserTool(
            {
                "https://target.test/": _FakeCapture("https://target.test/", homepage_html),
                "https://target.test/login": _FakeCapture("https://target.test/login", "<html></html>"),
                "https://target.test/about": _FakeCapture("https://target.test/about", "<html></html>"),
            }
        )
        result = asyncio.run(build_mental_model("https://target.test/", tool))
        assert set(tool.calls) == {
            "https://target.test/",
            "https://target.test/login",
            "https://target.test/about",
        }
        assert result.pages_analyzed == 3

    def test_calls_trace_page_for_every_fetched_page(self, mocker):
        _mock_groq_pipeline(mocker)
        mock_trace = mocker.patch(
            "core.mental_model.builder.trace_page", side_effect=_empty_page_signals
        )
        homepage_html = '<a href="/a">A</a>'
        tool = _FakeBrowserTool(
            {
                "https://target.test/": _FakeCapture("https://target.test/", homepage_html),
                "https://target.test/a": _FakeCapture("https://target.test/a", "<html></html>"),
            }
        )
        asyncio.run(build_mental_model("https://target.test/", tool))
        assert mock_trace.call_count == 2


class TestBuildMentalModelPartialAbort:
    def test_a_single_out_of_scope_link_is_skipped_not_fatal(self, mocker):
        _mock_groq_pipeline(mocker)
        mocker.patch("core.mental_model.builder.trace_page", side_effect=_empty_page_signals)
        homepage_html = '<a href="/blocked">Blocked</a><a href="/ok">OK</a>'
        tool = _FakeBrowserTool(
            {
                "https://target.test/": _FakeCapture("https://target.test/", homepage_html),
                "https://target.test/blocked": RuntimeError("out of scope"),
                "https://target.test/ok": _FakeCapture("https://target.test/ok", "<html></html>"),
            }
        )
        result = asyncio.run(build_mental_model("https://target.test/", tool))
        assert result.pages_analyzed == 2  # homepage + /ok, /blocked skipped
        assert result.is_partial is True  # fewer pages fetched than selected

    def test_per_page_timeout_marks_partial_and_continues(self, mocker):
        _mock_groq_pipeline(mocker)
        mocker.patch("core.mental_model.builder.trace_page", side_effect=_empty_page_signals)
        mocker.patch("core.mental_model.builder.PER_PAGE_TIMEOUT_SECONDS", 0.05)
        mocker.patch("core.mental_model.builder.TOTAL_TIMEOUT_SECONDS", 10.0)
        homepage_html = '<a href="/slow">Slow</a><a href="/fast">Fast</a>'
        tool = _FakeBrowserTool(
            {
                "https://target.test/": _FakeCapture("https://target.test/", homepage_html),
                "https://target.test/slow": _FakeCapture("https://target.test/slow", "<html></html>"),
                "https://target.test/fast": _FakeCapture("https://target.test/fast", "<html></html>"),
            },
            delays={"https://target.test/slow": 0.2},  # only the sub-page is slow, not the homepage
        )
        result = asyncio.run(build_mental_model("https://target.test/", tool))
        assert result.is_partial is True
        assert result.pages_analyzed == 2  # homepage + /fast; /slow timed out and was skipped

    def test_total_timeout_stops_fetching_further_pages(self, mocker):
        _mock_groq_pipeline(mocker)
        mocker.patch("core.mental_model.builder.trace_page", side_effect=_empty_page_signals)
        mocker.patch("core.mental_model.builder.TOTAL_TIMEOUT_SECONDS", 0.0)
        mocker.patch("core.mental_model.builder.PER_PAGE_TIMEOUT_SECONDS", 5.0)
        homepage_html = "".join(f'<a href="/p{i}">P{i}</a>' for i in range(6))
        pages = {f"https://target.test/p{i}": _FakeCapture(f"https://target.test/p{i}", "<html></html>") for i in range(6)}
        pages["https://target.test/"] = _FakeCapture("https://target.test/", homepage_html)
        tool = _FakeBrowserTool(pages)

        result = asyncio.run(build_mental_model("https://target.test/", tool))

        # Budget already exhausted (0.0s) before any page after the
        # homepage is fetched -- only the homepage itself is analyzed.
        assert result.pages_analyzed == 1
        assert result.is_partial is True

    def test_complete_fetch_of_all_selected_pages_is_not_partial(self, mocker):
        _mock_groq_pipeline(mocker)
        mocker.patch("core.mental_model.builder.trace_page", side_effect=_empty_page_signals)
        tool = _FakeBrowserTool({"https://target.test/": _FakeCapture("https://target.test/", "<html></html>")})
        result = asyncio.run(build_mental_model("https://target.test/", tool))
        assert result.is_partial is False


class TestBuildMentalModelGroqFailuresPropagate:
    """builder.py's own docstring: Groq-stage failures are NOT caught
    or degraded here, unlike per-page local-7B failures."""

    def test_boundary_identifier_failure_propagates(self, mocker):
        from core.mental_model._groq_client import GroqCallError

        mocker.patch("core.mental_model.builder.trace_page", side_effect=_empty_page_signals)
        mocker.patch(
            "core.mental_model.builder.boundary_identifier.identify_boundaries",
            side_effect=GroqCallError("boom"),
        )
        tool = _FakeBrowserTool({"https://target.test/": _FakeCapture("https://target.test/", "<html></html>")})
        with pytest.raises(GroqCallError):
            asyncio.run(build_mental_model("https://target.test/", tool))
