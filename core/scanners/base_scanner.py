"""
Implements: Section 3 -- core/scanners/base_scanner.py's HTTP
ENFORCEMENT CONTRACT (R-L6 fix): "ALL scanner HTTP calls MUST use
self.session (RateLimitedClient, which wraps InterceptingClient and
scope_enforcer.is_allowed()). Direct imports of httpx, requests,
urllib in scanner files = BUILD FAILURE." Also implements the
Engineering Constitution's "[BASE_SCANNER IS THE ONLY SCANNER
INTERFACE]" mandate and Section 10.2 item 1 (RateLimitedClient as a
scope-enforcement layer, reached here via `self.session`).
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE (docs/DECISIONS.md item 73, resolving this file's own prior
deferral): this class carries the HTTP-enforcement contract (unchanged)
PLUS, as of Week 7, the one abstract `scan()` method Section 3 never
named a shape for. The deferral reasoning below is preserved for the
record, not deleted -- it explains WHY no guessed signature existed
before this week, which is still true history even though the gap it
describes is now closed.

ORIGINAL (Week 5) SCOPE NOTE: this class carried ONLY the HTTP-
enforcement contract -- Section 3's one actually-specified piece -- and
nothing else. It had NO scan-execution method (`scan()`, `execute()`,
`run()`, or any other name) -- not even as an abstract stub with a
placeholder signature. That was a deliberate absence, not an oversight:
no section anywhere gives that method's name, parameters, or return
type, and a guessed signature (e.g. `def scan(self, target: str) ->
list[Any]`) would read, to a future implementer, as a real decision
rather than the guess it would be -- exactly the failure mode "STOP AND
ASK... never invent a plausible default silently" exists to prevent.
The method's real shape depended in part on `ExploitCandidate`'s
fields, which docs/DECISIONS.md item 53 left PROVISIONAL and
unspecified on purpose; specifying this method before that settled
would have been inventing on top of an already-flagged unknown.

WEEK 7: `ExploitCandidate` now exists, confirmed and non-PROVISIONAL
(docs/DECISIONS.md item 69, `core/ontology/findings.py`) -- the
prerequisite the note above named. `scan()`'s return type is `list[
ExploitCandidate]` per the build order's explicit instruction. Its
parameter, `target_url: str`, is this file's own authored addition, not
independently blueprint-cited beyond Section 6.5's general "SolverPool
runs 29 scanners in parallel" framing -- kept to the single, minimal
input every one of the 29 workflows in Section 7.1-7.29 actually
describes testing (one endpoint at a time; a single scanner instance
may still produce zero, one, or many `ExploitCandidate`s from one
`target_url`, e.g. one per parameter or per payload variant it tries
against that URL). Deliberately does NOT take an `EndpointSignals`,
`SurfaceData`, or any other richer object -- `SurfaceData` stays exactly
as PROVISIONAL as docs/DECISIONS.md items 10/53/63/70 already left it
(item 70's own scoping decision), and `EndpointSignals` (item 70) is a
per-endpoint AGGREGATE that scanners contribute readings TO, not an
input they read FROM to decide what to test. `async def`, not a plain
`def`: every one of the 29 scanners will need to `await
self.session.request(...)` (`RateLimitedClient.request` is itself
`async def`, confirmed by direct read before this signature was
chosen), so a synchronous `scan()` could not call it.

`abc.ABC` IS USED WITH AN `@abstractmethod`, AS OF THIS WEEK: Week 5's
version used `ABC` without one (see below) since nothing existed yet to
force subclasses to implement. `scan()` is that thing now -- every one
of the 29 scanners (Week 7's batches) must implement it, and Python's
`ABC` machinery enforces that at class-definition time (a subclass
missing `scan()` cannot be instantiated) rather than only at first call.

ORIGINAL (Week 5) `abc.ABC` NOTE, still accurate as history: Python
permits `ABC` without `@abstractmethod`; `ABC` alone does not by itself
prevent instantiation unless at least one method is marked abstract.
Used anyway (rather than a plain `class BaseScanner:`) to signal the
inheritance CONTRACT the Constitution names ("All 29 scanners inherit
the same abstract base") even before there was anything to force
subclasses to implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.http.rate_limited_client import RateLimitedClient
from core.ontology.findings import ExploitCandidate


class BaseScanner(ABC):
    """The one interface all 29 scanners (Week 7) inherit from.

    Orchestration code (a future `SolverPool`, `ToolSelector`,
    `SCANNER_REGISTRY`) must never special-case an individual scanner by
    name -- Engineering Constitution, "[BASE_SCANNER IS THE ONLY SCANNER
    INTERFACE]": "Writing `if scanner_name == 'xss_scanner':` anywhere
    outside base_scanner.py itself is a reusability violation."

    Attributes:
        session: A `RateLimitedClient`. Every scanner's own HTTP calls
            MUST go through this -- direct `httpx`/`requests`/`urllib`
            imports in `core/scanners/` are a build failure, enforced by
            `make ci-scanner-http-check` (root `Makefile`) and
            `tests/core/scanners/test_ci_hooks.py`, mirroring this
            requirement's exact grep pattern from Section 3's own
            comment: `grep -r "^import httpx\\|^from httpx\\|^import
            requests" core/scanners/` must return empty.
    """

    def __init__(self, session: RateLimitedClient) -> None:
        """
        Args:
            session: The `RateLimitedClient` this scanner must use for
                every outbound HTTP call.
        """
        self.session = session

    @abstractmethod
    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """Tests `target_url` for this scanner's vuln_type, using only
        `self.session` for outbound HTTP (the class docstring's
        contract). See this module's docstring, "WEEK 7," for why the
        signature takes exactly this shape and nothing richer.

        Args:
            target_url: The single endpoint to test.

        Returns:
            Zero, one, or many `ExploitCandidate`s -- one scan of one
            `target_url` may produce several (e.g. one per parameter or
            per payload variant this scanner tries against it). Never
            `None`; an empty list, not `None`, represents "nothing
            found" (matches `lfi_scanner.py`'s own Section 7.10 wording,
            "returns 0 ExploitCandidates," not "returns None").
        """
        raise NotImplementedError
