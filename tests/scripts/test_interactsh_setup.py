"""
Implements: Section 3 test coverage -- interactsh_setup.py
Blueprint: bb_agent_v6.6_final_blueprint.md

Public-interactsh network calls are mocked -- this sandbox's network
allowlist does not include interactsh.com. Live verification should be
re-run in an environment with that egress before Week 0 sign-off.
"""

import pytest
import requests

from scripts.interactsh_setup import (
    InteractshMode,
    check_public_interactsh,
    run_setup,
    try_self_hosted_fallback,
)


class _FakeSession:
    """Injectable stand-in for `requests` -- returns canned responses or
    raises canned exceptions per call, in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class TestCheckPublicInteractsh:
    def test_200_counts_as_responding(self):
        session = _FakeSession([_FakeResponse(200)])
        ok, status, detail = check_public_interactsh(session)
        assert ok is True
        assert status == 200

    def test_429_is_not_ok_and_reports_status(self):
        session = _FakeSession([_FakeResponse(429)])
        ok, status, detail = check_public_interactsh(session)
        assert ok is False
        assert status == 429
        assert "rate limited" in detail

    def test_network_error_is_not_ok(self):
        session = _FakeSession([requests.ConnectionError("no route")])
        ok, status, detail = check_public_interactsh(session)
        assert ok is False
        assert status is None
        assert "network error" in detail

    def test_5xx_is_not_ok(self):
        session = _FakeSession([_FakeResponse(503)])
        ok, status, detail = check_public_interactsh(session)
        assert ok is False
        assert status == 503


class TestTrySelfHostedFallback:
    def test_success(self):
        started, detail = try_self_hosted_fallback(lambda: True)
        assert started is True
        assert "started" in detail

    def test_failure_returns_false_not_raise(self):
        started, detail = try_self_hosted_fallback(lambda: False)
        assert started is False
        assert "failed to start" in detail

    def test_exception_in_start_fn_is_caught(self):
        def _boom():
            raise RuntimeError("binary not found")

        started, detail = try_self_hosted_fallback(_boom)
        assert started is False
        assert "raised" in detail


class TestRunSetup:
    def test_public_success_on_first_try(self):
        session = _FakeSession([_FakeResponse(200)])
        result = run_setup(session=session, retry_delay_seconds=0)
        assert result.mode == InteractshMode.PUBLIC
        assert result.preflight_ok is True

    def test_falls_back_to_self_hosted_after_3_consecutive_429s(self):
        session = _FakeSession([_FakeResponse(429), _FakeResponse(429), _FakeResponse(429)])
        result = run_setup(
            session=session,
            retry_delay_seconds=0,
            self_hosted_start_fn=lambda: True,
        )
        assert result.mode == InteractshMode.SELF_HOSTED
        assert result.preflight_ok is True
        assert session.calls == 3

    def test_unavailable_when_public_and_self_hosted_both_fail(self):
        session = _FakeSession([_FakeResponse(429), _FakeResponse(429), _FakeResponse(429)])
        result = run_setup(
            session=session,
            retry_delay_seconds=0,
            self_hosted_start_fn=lambda: False,
        )
        assert result.mode == InteractshMode.UNAVAILABLE
        assert result.preflight_ok is False

    def test_unavailable_when_no_self_hosted_launcher_provided(self):
        # Week 0: the real self-hosted Go client binary doesn't exist yet.
        session = _FakeSession([requests.ConnectionError("sandboxed: no egress")])
        result = run_setup(session=session, retry_delay_seconds=0, self_hosted_start_fn=None)
        assert result.mode == InteractshMode.UNAVAILABLE
        assert result.preflight_ok is False
        assert "Week 0" in result.detail

    def test_non_429_failure_does_not_retry_the_full_limit(self):
        # A single ConnectionError, not 3 -- confirms we break out early
        # rather than looping the full consecutive_429_limit on a
        # failure mode retrying won't fix.
        session = _FakeSession([requests.ConnectionError("dns failure")])
        result = run_setup(session=session, retry_delay_seconds=0, self_hosted_start_fn=lambda: True)
        assert session.calls == 1
        assert result.mode == InteractshMode.SELF_HOSTED
