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
from core.scanners.base_scanner import BaseScanner


def _session() -> RateLimitedClient:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
    return RateLimitedClient(scope_domains=["example.com"], intercepting_client=InterceptingClient(transport=transport))


class TestBaseScanner:
    def test_is_an_abc_subclass(self):
        """Signals the inheritance contract (module docstring) even
        without an @abstractmethod to enforce it yet."""
        assert issubclass(BaseScanner, ABC)

    def test_stores_session_as_is(self):
        session = _session()

        class _ConcreteScanner(BaseScanner):
            pass

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

    def test_deliberately_has_no_scan_or_execute_method(self):
        """Pins the deliberate absence (module docstring): no
        scan()/execute()/run() method exists yet. If this test ever
        needs updating, that update should be a conscious, documented
        decision (a real method signature, cited or flagged), not an
        incidental side effect of adding one without noticing this pin."""
        for forbidden_name in ("scan", "execute", "run"):
            assert not hasattr(BaseScanner, forbidden_name), (
                f"BaseScanner unexpectedly has a '{forbidden_name}' method -- "
                "this was deliberately left absent (see module docstring); "
                "if it's now been added, update this test as a conscious decision."
            )

    def test_concrete_subclass_is_directly_instantiable(self):
        """No @abstractmethod exists yet, so ABC alone does not prevent
        instantiation -- documented in the module docstring, confirmed
        here rather than assumed."""
        class _ConcreteScanner(BaseScanner):
            pass

        _ConcreteScanner(_session())  # must not raise
