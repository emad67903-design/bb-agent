"""
Implements: Section 4.4's `SCANNER_REGISTRY` ("caller_id is set once, at
SCANNER_REGISTRY instantiation time, using each scanner's own registry
key (e.g. 'xss_scanner', 'hardcoded_credentials') -- carried on the
scanner's RateLimitedClient instance"). Also implements the Engineering
Constitution's "[SCANNER_REGISTRY IS THE ONLY LOOKUP PATH]" mandate:
"No file imports a scanner class directly to instantiate it. Every
lookup goes through SCANNER_REGISTRY[scanner_id]."
Blueprint: bb_agent_v6.6_final_blueprint.md

FILE LOCATION (docs/DECISIONS.md, Week 5 tool-ify scoping entry):
`core/scanners/registry.py`, not `core/scanners/__init__.py`. No
blueprint section gives `SCANNER_REGISTRY` a file path at all (grep-
confirmed: all three mentions are the same sentence about `caller_id`,
never a `Section 3`-style tree entry). This project's own convention
keeps `__init__.py` minimal/export-only; a registry with real
registration logic (a dict, a `register()` decorator, lookup by
`scanner_id`) is substantial enough to earn its own named file, the same
as `token_throttler.py` or `session_persistence.py` did rather than
living in an `__init__.py`.

EMPTY AT THE END OF WEEK 5, BY DESIGN, NOT AS A GAP: `SCANNER_REGISTRY`
starts and stays an empty dict this week. No scanner exists to register
until Week 7 builds the 29 concrete scanner classes (Section 12: "All 29
scanner harnesses," Week 7's own row) -- Week 5 does NOT deliver "29
scanners as tools" in any literal sense; it delivers the MECHANISM
(this registry + `base_scanner.py`'s contract + `RateLimitedClient`'s
`caller_id` wiring) that makes a scanner registrable as a scope-safe,
uniquely identified tool once Week 7 actually writes one. Tested here
using a dummy `BaseScanner` subclass, never a real vulnerability
scanner, since none exist yet.
"""

from __future__ import annotations

from typing import Callable

from core.http.rate_limited_client import RateLimitedClient
from core.scanners.base_scanner import BaseScanner

SCANNER_REGISTRY: dict[str, type[BaseScanner]] = {}


class ScannerAlreadyRegistered(Exception):
    """Raised by `register` when `scanner_id` is already in `SCANNER_REGISTRY`."""


class ScannerNotRegistered(Exception):
    """Raised by `create_scanner` when `scanner_id` is not in `SCANNER_REGISTRY`."""


def register(scanner_id: str) -> Callable[[type[BaseScanner]], type[BaseScanner]]:
    """Decorator: registers a `BaseScanner` subclass under `scanner_id`.

    Week 7's intended usage, per Section 4.4's own examples
    (`"xss_scanner"`, `"hardcoded_credentials"`):

        @register("xss_scanner")
        class XSSScanner(BaseScanner):
            ...

    Args:
        scanner_id: The registry key this scanner will be looked up and
            identified by -- also becomes its `RateLimitedClient.caller_id`
            (see `create_scanner`).

    Returns:
        A decorator that registers the class and returns it unmodified.

    Raises:
        ScannerAlreadyRegistered: If `scanner_id` is already registered
            -- "one source of truth for which scanners exist" (Engineering
            Constitution); this project's own history already had to fix
            one duplicated, drifted scanner list (Tier C `auto_allow`,
            29-vs-28 bug, docs/DECISIONS.md item 22) and this check exists
            so a second copy of that failure mode can't happen here.
    """

    def decorator(scanner_cls: type[BaseScanner]) -> type[BaseScanner]:
        if scanner_id in SCANNER_REGISTRY:
            raise ScannerAlreadyRegistered(
                f"{scanner_id!r} is already registered to {SCANNER_REGISTRY[scanner_id].__name__}; "
                f"cannot also register {scanner_cls.__name__}"
            )
        SCANNER_REGISTRY[scanner_id] = scanner_cls
        return scanner_cls

    return decorator


def create_scanner(
    scanner_id: str,
    *,
    scope_domains: list[str],
    requests_per_second: float = 10.0,
) -> BaseScanner:
    """Looks up `scanner_id`, builds its `RateLimitedClient` with
    `caller_id=scanner_id`, and instantiates the scanner with it.

    This is the concrete mechanism behind Section 4.4's description:
    "`caller_id` is set once, at `SCANNER_REGISTRY` instantiation
    time... carried on the scanner's `RateLimitedClient` instance" --
    this function IS that instantiation moment.

    Args:
        scanner_id: The registry key (e.g. `"xss_scanner"`).
        scope_domains: The program's in-scope domain patterns, passed
            straight through to the scanner's `RateLimitedClient`.
        requests_per_second: Passed straight through to the scanner's
            `RateLimitedClient`. Defaults to 10.0 (Section 10.3).

    Returns:
        A new instance of the scanner class registered under
        `scanner_id`, with a fresh `RateLimitedClient` (this specific
        `caller_id`, not shared with any other scanner's client) as its
        `session`.

    Raises:
        ScannerNotRegistered: If `scanner_id` is not in
            `SCANNER_REGISTRY`.
    """
    if scanner_id not in SCANNER_REGISTRY:
        raise ScannerNotRegistered(f"No scanner registered under {scanner_id!r}")

    scanner_cls = SCANNER_REGISTRY[scanner_id]
    session = RateLimitedClient(
        scope_domains=scope_domains,
        caller_id=scanner_id,
        requests_per_second=requests_per_second,
    )
    return scanner_cls(session)
