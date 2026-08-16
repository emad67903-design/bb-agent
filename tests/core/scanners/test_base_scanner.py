"""
Implements: Section 3 test coverage -- core/scanners/base_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

from abc import ABC

import httpx
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner


def _session() -> RateLimitedClient:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
    return RateLimitedClient(scope_domains=["example.com"], intercepting_client=InterceptingClient(transport=transport))


class _ConcreteScanner(BaseScanner):
    """Minimal real implementation of `scan()`, for tests needing an
    instantiable subclass (docs/DECISIONS.md item 73)."""

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        return []


class TestBaseScanner:
    def test_is_an_abc_subclass(self):
        """Signals the inheritance contract (module docstring), now
        enforced by a real @abstractmethod (item 73) rather than just
        declared."""
        assert issubclass(BaseScanner, ABC)

    def test_stores_session_as_is(self):
        session = _session()
        scanner = _ConcreteScanner(session)
        assert scanner.session is session  # identity, not just equality

    def test_session_must_be_a_rate_limited_client_by_convention(self):
        """Not runtime-enforced (no isinstance check in __init__ --
        Python's duck typing plus the CI import-ban together are the
        actual enforcement mechanism, matching Section 3's own two-part
        contract: self.session as a RateLimitedClient by convention,
        with direct httpx/requests imports caught by CI, not a runtime
        type check). This test documents that expectation."""
        session = _session()
        assert isinstance(session, RateLimitedClient)

    def test_scan_is_abstract_and_declared_on_base_scanner(self):
        """Supersedes the old `test_deliberately_has_no_scan_or_execute_method`
        pin (docs/DECISIONS.md item 73, a conscious update per that
        test's own docstring, not an incidental side effect): `scan()`
        is now deliberately PRESENT, not deliberately absent. Pins the
        new fact the same way the old test pinned the old one."""
        assert hasattr(BaseScanner, "scan")
        assert getattr(BaseScanner.scan, "__isabstractmethod__", False) is True

    def test_subclass_without_scan_is_not_instantiable(self):
        """The behavioral consequence of `@abstractmethod` (item 73):
        a subclass that does not implement `scan()` cannot be
        constructed at all, enforced by Python's ABC machinery at
        instantiation time -- this is the replacement for what
        `test_concrete_subclass_is_directly_instantiable` used to pin
        (the OPPOSITE fact, true only before this week)."""

        class _IncompleteScanner(BaseScanner):
            pass

        with pytest.raises(TypeError, match="scan"):
            _IncompleteScanner(_session())

    def test_concrete_subclass_implementing_scan_is_instantiable(self):
        """A subclass that DOES implement `scan()` is unaffected by the
        `@abstractmethod` -- confirms the guard is specific to the
        missing implementation, not a general instantiation block."""
        _ConcreteScanner(_session())  # must not raise

    @pytest.mark.asyncio
    async def test_scan_signature_accepts_target_url_and_returns_list(self):
        """Pins `scan()`'s settled shape (docs/DECISIONS.md item 73):
        `async def scan(self, target_url: str) -> list[ExploitCandidate]`."""
        scanner = _ConcreteScanner(_session())
        result = await scanner.scan("https://example.com/search")
        assert result == []
