"""
Implements: Section 4.4 test coverage -- core/scanners/registry.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import pytest

import core.scanners.registry as registry_module
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import (
    ScannerAlreadyRegistered,
    ScannerNotRegistered,
    create_scanner,
    register,
)


@pytest.fixture(autouse=True)
def isolated_registry():
    """`SCANNER_REGISTRY` is a mutable module-level global -- without
    this, one test registering a scanner would leak into every other
    test in the process, causing order-dependent failures. Snapshots and
    restores it around every test in this file."""
    original = dict(registry_module.SCANNER_REGISTRY)
    registry_module.SCANNER_REGISTRY.clear()
    yield
    registry_module.SCANNER_REGISTRY.clear()
    registry_module.SCANNER_REGISTRY.update(original)


class _DummyScannerA(BaseScanner):
    """Minimal real `scan()` (docs/DECISIONS.md item 73) -- this file's
    tests only care about registry/instantiation mechanics, not
    scanner-specific detection logic, so an empty result is sufficient
    and keeps every existing test below unchanged."""

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        return []


class _DummyScannerB(BaseScanner):
    """See `_DummyScannerA`."""

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        return []


class TestRegister:
    def test_registers_class_under_given_id(self):
        register("dummy_a")(_DummyScannerA)
        assert registry_module.SCANNER_REGISTRY["dummy_a"] is _DummyScannerA

    def test_usable_as_a_decorator(self):
        @register("dummy_a")
        class LocalScanner(BaseScanner):
            pass

        assert registry_module.SCANNER_REGISTRY["dummy_a"] is LocalScanner

    def test_returns_the_class_unmodified(self):
        result = register("dummy_a")(_DummyScannerA)
        assert result is _DummyScannerA

    def test_duplicate_registration_raises(self):
        register("dummy_a")(_DummyScannerA)
        with pytest.raises(ScannerAlreadyRegistered, match="dummy_a"):
            register("dummy_a")(_DummyScannerB)

    def test_duplicate_registration_does_not_overwrite(self):
        register("dummy_a")(_DummyScannerA)
        with pytest.raises(ScannerAlreadyRegistered):
            register("dummy_a")(_DummyScannerB)
        assert registry_module.SCANNER_REGISTRY["dummy_a"] is _DummyScannerA

    def test_different_ids_coexist(self):
        register("dummy_a")(_DummyScannerA)
        register("dummy_b")(_DummyScannerB)
        assert registry_module.SCANNER_REGISTRY == {"dummy_a": _DummyScannerA, "dummy_b": _DummyScannerB}


class TestCreateScanner:
    def test_creates_instance_of_registered_class(self):
        register("dummy_a")(_DummyScannerA)
        scanner = create_scanner("dummy_a", scope_domains=["example.com"])
        assert isinstance(scanner, _DummyScannerA)

    def test_caller_id_set_to_scanner_id(self):
        """The concrete embodiment of Section 4.4's 'caller_id set once,
        at SCANNER_REGISTRY instantiation time.'"""
        register("dummy_a")(_DummyScannerA)
        scanner = create_scanner("dummy_a", scope_domains=["example.com"])
        assert scanner.session.caller_id == "dummy_a"

    def test_scope_domains_passed_through(self):
        register("dummy_a")(_DummyScannerA)
        scanner = create_scanner("dummy_a", scope_domains=["*.example.com"])
        assert scanner.session._scope_domains == ["*.example.com"]

    def test_each_call_gets_its_own_rate_limited_client(self):
        register("dummy_a")(_DummyScannerA)
        scanner1 = create_scanner("dummy_a", scope_domains=["example.com"])
        scanner2 = create_scanner("dummy_a", scope_domains=["example.com"])
        assert scanner1.session is not scanner2.session

    def test_unregistered_id_raises(self):
        with pytest.raises(ScannerNotRegistered, match="nonexistent_scanner"):
            create_scanner("nonexistent_scanner", scope_domains=["example.com"])

    def test_custom_requests_per_second_passed_through(self):
        register("dummy_a")(_DummyScannerA)
        scanner = create_scanner("dummy_a", scope_domains=["example.com"], requests_per_second=5.0)
        assert scanner.session._rate_limiter._min_interval == pytest.approx(0.2)


class TestRegistryStartsEmptyInProduction:
    def test_module_level_registry_is_a_dict(self):
        """Confirms the underlying object type directly, independent of
        the isolated_registry fixture's own clearing behavior."""
        assert isinstance(registry_module.SCANNER_REGISTRY, dict)


class _DummyScannerWithExtra(BaseScanner):
    """Mirrors the real per-scanner optional-kwarg pattern Batch 1
    already established (e.g. `xss_scanner.py`'s `payloads` param,
    docs/DECISIONS.md items 74-80) -- used here to prove
    `create_scanner`'s `**scanner_kwargs` passthrough (item 82) is
    generic, not hardcoded to any one scanner or parameter name."""

    def __init__(self, session, *, extra: object | None = None) -> None:
        super().__init__(session)
        self.extra = extra

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        return []


class _DummyScannerWithTwoExtras(BaseScanner):
    """See `_DummyScannerWithExtra`. Two extra kwargs, to prove
    forwarding isn't limited to a single keyword."""

    def __init__(self, session, *, extra: object | None = None, another: object | None = None) -> None:
        super().__init__(session)
        self.extra = extra
        self.another = another

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        return []


class TestCreateScannerKwargsPassthrough:
    """docs/DECISIONS.md item 82: `create_scanner` forwards any extra
    keyword arguments verbatim to the scanner class's own `__init__`,
    after `session` -- the mechanism that lets a scanner reach a
    shared, session-level dependency (e.g. `InteractshClient`, item 81,
    Batch 2) without touching `BaseScanner.__init__`, the interface all
    29 scanners share.
    """

    def test_extra_kwarg_forwarded_to_scanner_constructor(self):
        register("dummy_extra")(_DummyScannerWithExtra)
        sentinel = object()
        scanner = create_scanner("dummy_extra", scope_domains=["example.com"], extra=sentinel)
        assert scanner.extra is sentinel

    def test_omitted_extra_kwarg_uses_scanner_own_default(self):
        register("dummy_extra")(_DummyScannerWithExtra)
        scanner = create_scanner("dummy_extra", scope_domains=["example.com"])
        assert scanner.extra is None

    def test_multiple_extra_kwargs_all_forwarded(self):
        register("dummy_two_extras")(_DummyScannerWithTwoExtras)
        sentinel_a, sentinel_b = object(), object()
        scanner = create_scanner(
            "dummy_two_extras",
            scope_domains=["example.com"],
            extra=sentinel_a,
            another=sentinel_b,
        )
        assert scanner.extra is sentinel_a
        assert scanner.another is sentinel_b

    def test_scanner_not_opting_in_is_unaffected_when_kwarg_omitted(self):
        """`_DummyScannerA` (this file's original dummy, no `__init__`
        override at all -- uses `BaseScanner.__init__` directly)
        continues to construct exactly as before. Proves the
        passthrough is opt-in per scanner, not a change to the shared
        `BaseScanner` interface every scanner inherits."""
        register("dummy_a")(_DummyScannerA)
        scanner = create_scanner("dummy_a", scope_domains=["example.com"])
        assert isinstance(scanner, _DummyScannerA)

    def test_kwarg_unsupported_by_scanner_raises_type_error_not_swallowed(self):
        """No silent absorption: a kwarg the target scanner's own
        `__init__` doesn't declare fails loudly, the same as calling any
        Python constructor with an unexpected keyword argument --
        `create_scanner` does not catch or translate this."""
        register("dummy_a")(_DummyScannerA)
        with pytest.raises(TypeError):
            create_scanner("dummy_a", scope_domains=["example.com"], extra=object())
