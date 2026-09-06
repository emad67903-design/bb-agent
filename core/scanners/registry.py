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

`create_scanner`'S `**scanner_kwargs` PASSTHROUGH (docs/DECISIONS.md
item 82 -- resolves the gap flagged ahead of Batch 2): item 81 built
`InteractshClient` as explicitly session-level, shared across every
scanner that needs OOB confirmation (SSRF, CMDi, XXE, Deserialization,
Host Header), not one-per-scanner -- but until this entry,
`create_scanner` called `scanner_cls(session)` with no way to hand a
scanner anything beyond its own fresh `RateLimitedClient`. No scanner
had any path to a shared `InteractshClient` instance.

TWO OPTIONS CONSIDERED. (1) Add `interactsh_client: InteractshClient |
None = None` to `BaseScanner.__init__` itself, threaded through here.
Rejected: `BaseScanner` is "the one interface all 29 scanners inherit
from" (this module's own docstring, above) -- of the 29, exactly 5 need
OOB (Section 5.3's achievability matrix: SSRF/CMDi/XXE/Deserialization/
Host Header are the only rows marked achievable-oob; grep-confirmed
against Section 5.3's table before this entry was written). Adding an
OOB-specific dependency to the shared base for the other 24 misreads
"the one interface" as "the one place for every dependency any scanner
might ever need" rather than what it actually names: the one thing
EVERY scanner needs (`session`). It also would not, by itself, solve
the reachability gap: every scanner built so far (Batch 1: `xss_
scanner.py` et al.) already defines its OWN `__init__` -- e.g.
`def __init__(self, session, *, payloads=None)` -- calling `super().
__init__(session)` and handling its own extra state. A parameter added
to `BaseScanner.__init__` is invisible to a subclass's own `__init__`
unless that subclass also declares and forwards it -- so Batch 2's five
scanners would need their own constructor changes regardless of where
the parameter lives. Option (1) would touch the shared interface for no
reachability benefit over option (2), while adding an unused attribute
to 24 scanners that will never read it.

(2) CHOSEN: `create_scanner` gains a generic `**scanner_kwargs`,
forwarded verbatim to `scanner_cls(session, **scanner_kwargs)`.
`BaseScanner.__init__` is untouched -- zero lines changed, confirmed by
this entry's own diff. Each scanner that needs `interactsh_client`
declares it as its own keyword-only constructor parameter, exactly the
precedent Batch 1 already set with `payloads` (test-injectable,
production-defaulted). `create_scanner` itself stays completely
scanner-agnostic: it does not know or care which `scanner_id` accepts
which extra kwarg, matching the Engineering Constitution's "[SCANNER_
REGISTRY IS THE ONLY LOOKUP PATH]" mandate against special-casing an
individual scanner by name. A kwarg a given scanner's `__init__` does
not declare raises `TypeError` at the `scanner_cls(...)` call below --
ordinary Python constructor behavior, not something this function
catches, swallows, or translates.

Deliberately NOT built here: no Batch 2 scanner yet declares an
`interactsh_client` parameter (that is each of those five scanners' own
constructor work, not this registry's) -- this entry closes the
PLUMBING gap (a scanner CAN reach a shared instance once its own
`__init__` asks for one), not the scanners themselves.

Behavioral equivalence, proven, not assumed: `tests/core/scanners/
test_base_scanner.py`, `test_registry.py`'s pre-existing classes, and
all five Batch 1 scanner test files' pre-existing tests re-run
unmodified before and after this change -- same 97 test names, same
order, all passing both times (`scanner_kwargs` defaults to empty, so
`scanner_cls(session, **{})` is `scanner_cls(session)`, identical to
the prior call for every scanner that does not opt in).
"""

from __future__ import annotations

from typing import Any, Callable

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
    **scanner_kwargs: Any,
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
        **scanner_kwargs: Forwarded verbatim to the scanner class's own
            `__init__`, after `session` (docs/DECISIONS.md item 82).
            Each concrete scanner declares which extra keyword-only
            parameters, if any, its own constructor accepts -- e.g.
            `payloads` (Batch 1, items 74-80) or `interactsh_client`
            (Batch 2 scanners requiring OOB confirmation, item 82).
            This function has no knowledge of which `scanner_id`
            accepts which kwarg; forwarding blindly is what keeps it
            from special-casing an individual scanner by name
            (Engineering Constitution, "[SCANNER_REGISTRY IS THE ONLY
            LOOKUP PATH]"). Omitted entirely for a scanner that does not
            opt in -- identical to the pre-item-82 call. A kwarg the
            target scanner's own `__init__` does not declare raises
            `TypeError` at the instantiation below; not caught or
            translated here.

    Returns:
        A new instance of the scanner class registered under
        `scanner_id`, with a fresh `RateLimitedClient` (this specific
        `caller_id`, not shared with any other scanner's client) as its
        `session`.

    Raises:
        ScannerNotRegistered: If `scanner_id` is not in
            `SCANNER_REGISTRY`.
        TypeError: If `scanner_kwargs` contains a keyword the target
            scanner class's own `__init__` does not accept.
    """
    if scanner_id not in SCANNER_REGISTRY:
        raise ScannerNotRegistered(f"No scanner registered under {scanner_id!r}")

    scanner_cls = SCANNER_REGISTRY[scanner_id]
    session = RateLimitedClient(
        scope_domains=scope_domains,
        caller_id=scanner_id,
        requests_per_second=requests_per_second,
    )
    return scanner_cls(session, **scanner_kwargs)
