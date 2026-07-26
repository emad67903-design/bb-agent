"""
Implements: Section 3 / Section 8.3 (R-M5) / Section 10.4 test coverage
-- core/control/credential_lifecycle.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import base64
import time

import pytest

from core.control.credential_lifecycle import (
    CredentialLifecycle,
    TokenDecodeError,
    TwoFactorChallenge,
)


@pytest.fixture
def lifecycle():
    return CredentialLifecycle()


class TestNoPasswordStorage:
    """Section 10.4: 'No password field in Account.' This module stores
    tokens only -- these tests pin that as an enforced invariant, not
    just a docstring promise."""

    def test_store_rejects_password_key(self, lifecycle):
        with pytest.raises(ValueError, match="password"):
            lifecycle.store({"password": "hunter2"})

    def test_store_rejects_password_key_case_insensitive(self, lifecycle):
        with pytest.raises(ValueError):
            lifecycle.store({"UserPassword": "hunter2"})

    def test_store_accepts_ordinary_token_keys(self, lifecycle):
        lifecycle.store({"session_cookie": "abc", "access_token": "xyz"})
        assert lifecycle.get("session_cookie") == "abc"

    def test_encode_rejects_password_key(self):
        with pytest.raises(ValueError, match="password"):
            CredentialLifecycle.encode_tokens_for_auth_payload({"password": "hunter2"})

    def test_no_password_named_attribute_anywhere_on_the_class(self):
        """Structural check: no method/property name contains 'password'."""
        members = dir(CredentialLifecycle)
        assert not any("password" in m.lower() for m in members)


class TestRamOnlyTokenStore:
    def test_has_tokens_false_initially(self, lifecycle):
        assert lifecycle.has_tokens() is False

    def test_has_tokens_true_after_store(self, lifecycle):
        lifecycle.store({"access_token": "abc"})
        assert lifecycle.has_tokens() is True

    def test_get_missing_key_returns_none(self, lifecycle):
        assert lifecycle.get("nope") is None

    def test_clear_empties_store(self, lifecycle):
        lifecycle.store({"access_token": "abc"})
        lifecycle.clear()
        assert lifecycle.has_tokens() is False

    def test_store_merges_rather_than_replaces(self, lifecycle):
        lifecycle.store({"a": "1"})
        lifecycle.store({"b": "2"})
        assert lifecycle.get("a") == "1"
        assert lifecycle.get("b") == "2"

    def test_no_persistence_backing_store_is_a_plain_dict_in_ram(self, lifecycle):
        """Not a strong runtime guarantee (that's an architectural property
        of the whole process, not testable in isolation), but pins that
        the internal representation is a plain in-memory dict -- no file
        handle, no DB connection, no serialization anywhere in this class."""
        lifecycle.store({"access_token": "abc"})
        assert isinstance(lifecycle._tokens, dict)


class TestRefresh:
    def test_refresh_replaces_tokens_with_callback_result(self, lifecycle):
        lifecycle.store({"access_token": "old"})

        def refresher(current: dict[str, str]) -> dict[str, str]:
            assert current == {"access_token": "old"}
            return {"access_token": "new", "refresh_token": "r1"}

        lifecycle.refresh(refresher)
        assert lifecycle.get("access_token") == "new"
        assert lifecycle.get("refresh_token") == "r1"

    def test_refresh_callback_receiving_password_like_key_still_rejected(self, lifecycle):
        lifecycle.store({"access_token": "old"})
        with pytest.raises(ValueError, match="password"):
            lifecycle.refresh(lambda current: {"password": "hunter2"})


class TestTwoFactorQueue:
    def test_empty_queue_dequeue_returns_none(self, lifecycle):
        assert lifecycle.dequeue_2fa_challenge() is None

    def test_pending_count_zero_initially(self, lifecycle):
        assert lifecycle.pending_2fa_count == 0

    def test_enqueue_increments_count(self, lifecycle):
        lifecycle.enqueue_2fa_challenge(TwoFactorChallenge(challenge_id="c1", prompt="6-digit TOTP"))
        assert lifecycle.pending_2fa_count == 1

    def test_fifo_order(self, lifecycle):
        c1 = TwoFactorChallenge(challenge_id="c1", prompt="first")
        c2 = TwoFactorChallenge(challenge_id="c2", prompt="second")
        lifecycle.enqueue_2fa_challenge(c1)
        lifecycle.enqueue_2fa_challenge(c2)
        assert lifecycle.dequeue_2fa_challenge() is c1
        assert lifecycle.dequeue_2fa_challenge() is c2
        assert lifecycle.dequeue_2fa_challenge() is None

    def test_challenge_created_at_defaults_to_now(self):
        before = time.time()
        c = TwoFactorChallenge(challenge_id="c1", prompt="x")
        after = time.time()
        assert before <= c.created_at <= after


class TestCrashRecoveryMessage:
    """Section 8.3 / Section 3's exact SESSION_RESUME message format."""

    def test_message_matches_blueprint_format_verbatim(self):
        msg = CredentialLifecycle.build_session_resume_message("sess-123")
        assert msg == (
            "SESSION_RESUME sess-123: Tokens lost on crash. "
            "/auth sess-123 {base64_encoded_tokens} to re-auth."
        )

    def test_session_id_substituted_in_both_positions(self):
        msg = CredentialLifecycle.build_session_resume_message("abc")
        assert msg.count("abc") == 2

    def test_requires_reauth_true_when_no_tokens(self, lifecycle):
        assert lifecycle.requires_reauth is True

    def test_requires_reauth_false_once_tokens_present(self, lifecycle):
        lifecycle.store({"access_token": "x"})
        assert lifecycle.requires_reauth is False


class TestAuthPayloadRoundTrip:
    def test_encode_then_decode_round_trips(self, lifecycle):
        tokens = {"session_cookie": "abc123", "access_token": "xyz789"}
        payload = CredentialLifecycle.encode_tokens_for_auth_payload(tokens)
        lifecycle.restore_tokens_from_auth_payload(payload)
        assert lifecycle.get("session_cookie") == "abc123"
        assert lifecycle.get("access_token") == "xyz789"

    def test_restore_sets_has_tokens_true(self, lifecycle):
        payload = CredentialLifecycle.encode_tokens_for_auth_payload({"access_token": "x"})
        lifecycle.restore_tokens_from_auth_payload(payload)
        assert lifecycle.has_tokens() is True

    def test_invalid_base64_raises_token_decode_error(self, lifecycle):
        with pytest.raises(TokenDecodeError):
            lifecycle.restore_tokens_from_auth_payload("not valid base64 !!! ###")

    def test_valid_base64_but_no_equals_sign_raises(self, lifecycle):
        bad = base64.b64encode(b"just_a_token_no_kv").decode("ascii")
        with pytest.raises(TokenDecodeError, match="malformed"):
            lifecycle.restore_tokens_from_auth_payload(bad)

    def test_empty_payload_raises(self, lifecycle):
        empty = base64.b64encode(b"").decode("ascii")
        with pytest.raises(TokenDecodeError, match="no key=value"):
            lifecycle.restore_tokens_from_auth_payload(empty)

    def test_multiple_tokens_round_trip(self, lifecycle):
        tokens = {"a": "1", "b": "2", "c": "3"}
        payload = CredentialLifecycle.encode_tokens_for_auth_payload(tokens)
        lifecycle.restore_tokens_from_auth_payload(payload)
        for k, v in tokens.items():
            assert lifecycle.get(k) == v

    def test_encode_rejects_equals_in_key(self):
        with pytest.raises(ValueError):
            CredentialLifecycle.encode_tokens_for_auth_payload({"bad=key": "v"})
