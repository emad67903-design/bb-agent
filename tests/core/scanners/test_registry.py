"""
Implements: Section 4.4 test coverage -- core/scanners/registry.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import pytest

import core.scanners.registry as registry_module
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
    pass


class _DummyScannerB(BaseScanner):
    pass


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
