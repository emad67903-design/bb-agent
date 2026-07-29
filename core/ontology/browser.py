"""
Implements: Section 3 -- core/browser/browser_tool.py's
`async def capture(self, url: str) -> BrowserCapture:` return type.
Blueprint: bb_agent_v6.6_final_blueprint.md

PROVISIONAL SPEC -- proposed by implementer Claude, corrected and
confirmed by the advisor Claude (docs/DECISIONS.md item 32), pending
project-owner sign-off. Built on top of because blocking
`browser_tool.py` entirely is worse than a marked guess -- but treat
these fields as provisional, not settled, the same way `MentalModel`'s
own fields are marked in `core/ontology/mental_model.py`.

WEEK 3 SCOPE NOTE, CORRECTED (docs/DECISIONS.md item 32): this module's
first version argued `BrowserCapture` could be committed, unlike
`MentalModel`, because its "blast radius" (only `browser_tool.py` reads
it this week) was smaller. That was an inconsistent application of this
same session's own standard: `BrowserCapture` has NO field-level spec
anywhere in the blueprint (grep-confirmed -- one bare type-annotation
mention, Section 3, total), which is the same *category* of gap as
`MentalModel` (content unspecified), not the same category as
`scope_enforcer.py`/`token_throttler.py`/`BudgetProfile` (content fully
specified, only a week-tag missing, docs/DECISIONS.md items 4/9/31).
Blast radius affects how bad a wrong guess would be; it does not change
whether a guess is being made. It was still a guess. Corrected to
PROVISIONAL accordingly.

Confirmed field spec (docs/DECISIONS.md item 32): `requested_url: str`,
`final_url: str`, `html: str`, `status_code: int | None` -- kept
deliberately minimal because `JSAnalysisResult` (Section 3,
`core/ontology/surface.py`) already owns structured JS-specific
findings (endpoints, secrets, DOM sinks, frameworks); `BrowserCapture`
is only the raw page fetch feeding into it and into
`MentalModelBuilder`'s parsing, not a second home for those findings.

Placed here (`core/ontology/`), not in `core/browser/browser_tool.py`,
per the Engineering Constitution's ontology-first rule and the
`JSAnalysisResult` precedent (a JS/recon-parsing type that nonetheless
lives in `core/ontology/surface.py`, not a recon-specific directory) --
the same precedent that resolved `MentalModel`'s placement. Not placed
IN `surface.py` itself, since that file does not exist yet in this
repository and its full eventual scope (`EndpointSignals`, `SurfaceData`,
`AttackEdge`, `AttackGraph`, `ExploitCandidate` fields, `JSAnalysisResult`)
belongs to later weeks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserCapture:
    """PROVISIONAL (docs/DECISIONS.md item 32). Result of
    `browser_tool.py`'s `capture(url)`: what a scope-checked Playwright
    page load returned.

    Attributes:
        requested_url: The URL `capture()` was asked to visit.
        final_url: The URL the page ended on after any redirects
            (Playwright's `page.url` post-`goto()`). Equal to
            `requested_url` when no redirect occurred.
        html: The fully rendered page content (`page.content()`) --
            the entire reason to use a browser instead of a raw HTTP
            fetch is to observe post-JS-execution DOM state.
        status_code: The main navigation response's HTTP status code,
            or `None` if Playwright's `goto()` response object was
            unavailable (e.g. a same-document navigation).
    """

    requested_url: str
    final_url: str
    html: str
    status_code: int | None
