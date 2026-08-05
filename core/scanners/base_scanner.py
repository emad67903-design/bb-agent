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

SCOPE, DELIBERATELY NARROW (docs/DECISIONS.md, Week 5 tool-ify scoping
entry): this class carries the ONE piece of `base_scanner.py` Section 3
actually specifies -- the HTTP-enforcement contract -- and nothing else.
It has NO scan-execution method (`scan()`, `execute()`, `run()`, or any
other name) -- not even as an abstract stub with a placeholder
signature. This is a deliberate absence, not an oversight: no section
anywhere gives that method's name, parameters, or return type, and a
guessed signature (e.g. `def scan(self, target: str) -> list[Any]`)
would read, to a future implementer, as a real decision rather than the
guess it would be -- exactly the failure mode "STOP AND ASK... never
invent a plausible default silently" exists to prevent. The method's
real shape depends in part on `ExploitCandidate`'s fields, which
docs/DECISIONS.md item 53 left PROVISIONAL and unspecified on purpose;
specifying `base_scanner.py`'s scan method before that settles would be
inventing on top of an already-flagged unknown. Week 7 (the 29 scanner
harnesses' own week) is where a concrete method shape will actually be
forced by real usage, the same way `ExploitCandidate`'s fields are
expected to be.

`abc.ABC` IS USED WITHOUT AN `@abstractmethod` -- Python permits this;
`ABC` alone does not by itself prevent instantiation unless at least one
method is marked abstract. Used here anyway (rather than a plain
`class BaseScanner:`) to signal the inheritance CONTRACT the
Constitution names ("All 29 scanners inherit the same abstract base")
even though this class does not yet have anything to force subclasses
to implement.
"""

from __future__ import annotations

from abc import ABC

from core.http.rate_limited_client import RateLimitedClient


class BaseScanner(ABC):
    """The one interface all 29 scanners (Week 7) will inherit from.

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
