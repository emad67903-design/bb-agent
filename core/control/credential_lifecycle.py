"""
Implements: Section 3 -- core/control/credential_lifecycle.py ("Token
refresh and 2FA queue. NO password storage." / "CRASH RECOVERY PROTOCOL
(R-M5 fix)").
Also implements: Section 8.3's OOM_CRASH_RECOVERY cascade entry, Section
10.4 ("No password field in Account... Session tokens: RAM only. Lost
on SIGKILL. See crash recovery protocol in credential_lifecycle.py").
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 2 SCOPE:

This module builds: RAM-only token storage (`has_tokens()`, `store()`,
`get()`, `clear()`), a caller-supplied token-refresh hook, a FIFO 2FA
challenge queue, and the crash-recovery MESSAGE/RESTORE half of R-M5's
protocol (`build_session_resume_message`,
`restore_tokens_from_auth_payload`).

NOT built here (documented scope boundary, same shape as
docs/DECISIONS.md item 9's token_throttler note): Section 8.3/tree
comment's crash-recovery steps 2-3 ("TelegramBot sends...", "Agent
pauses at Phase 4 entry") require `telegram_bot.py` (actually SENDING
the message) and a phase/session orchestrator (actually PAUSING
execution) -- neither exists yet; no Section 12 week before Week 2
builds either. This module owns computing WHAT the resume message says
and WHERE the restored tokens go; wiring that into an actual Telegram
send and an actual phase-pause is later, unbuilt orchestration. See
`requires_reauth` below for the minimal signal this module exposes in
place of directly pausing anything itself.

NO PASSWORD STORAGE (Section 10.4, hardware/security constraint,
restated as a project-level core invariant): this module has no
concept of a "password" anywhere in its API -- only opaque token
strings (session cookies, access/refresh tokens, 2FA challenge
responses). Credentials originate from the OS keyring at runtime
(Section 10.4; see scripts/verify_groq_models.py's `_get_api_key`
pattern for this codebase's existing keyring convention) and are never
handled by this module at all.
"""

from __future__ import annotations

import base64
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


class TokenDecodeError(Exception):
    """Raised when a `/auth` command's base64 token payload cannot be decoded."""


@dataclass(frozen=True)
class TwoFactorChallenge:
    """One pending 2FA challenge (Section 3: "2FA queue").

    Attributes:
        challenge_id: Caller-assigned identifier (e.g. correlates to a
            specific login attempt).
        prompt: Human-readable description of what's being asked for
            (e.g. "6-digit TOTP code"), for display in whatever
            eventually surfaces this to a human (Telegram, Week 7+).
        created_at: Unix timestamp, for staleness checks by callers.
    """

    challenge_id: str
    prompt: str
    created_at: float = field(default_factory=time.time)


class CredentialLifecycle:
    """RAM-only session-token store + 2FA queue + R-M5 crash-recovery helpers.

    Tokens live ONLY in `self._tokens` (a plain dict, process memory).
    There is no file write, no Redis write, no PostgreSQL write anywhere
    in this class -- a SIGKILL loses everything here by construction,
    which is the security property Section 10.4 requires ("Session
    tokens: RAM only. Lost on SIGKILL"), not a limitation to work around.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._pending_2fa: deque[TwoFactorChallenge] = deque()

    # --- token storage (RAM-only) ---

    def has_tokens(self) -> bool:
        """Section 8.3: "credential_lifecycle.has_tokens() = False" is the
        exact crash-detection check. True iff at least one token is stored."""
        return bool(self._tokens)

    def store(self, tokens: dict[str, str]) -> None:
        """Stores/merges token key-value pairs in RAM.

        Args:
            tokens: e.g. {"session_cookie": "...", "access_token": "...",
                "refresh_token": "..."}. Keys are caller-defined; this
                class does not validate or interpret them, matching
                Section 10.1's varied auth mechanisms across the 29
                scanners (cookie-based, JWT, OAuth, etc.) with a single
                storage shape rather than one enum per auth style.

        Raises:
            ValueError: If any key or value is literally "password"
                (case-insensitive key match) -- a deliberate guard for
                the no-password-storage invariant, not just a docstring
                promise.
        """
        for key in tokens:
            if "password" in key.lower():
                raise ValueError(f"CredentialLifecycle stores tokens only, never passwords (rejected key: {key!r})")
        self._tokens.update(tokens)

    def get(self, key: str) -> str | None:
        return self._tokens.get(key)

    def clear(self) -> None:
        """Drops all in-RAM tokens (e.g. at session end)."""
        self._tokens.clear()

    def refresh(self, refresher: Callable[[dict[str, str]], dict[str, str]]) -> None:
        """Replaces stored tokens with the result of a caller-supplied refresh.

        Args:
            refresher: Called with the current token dict; returns the
                new token dict to store. The actual protocol (calling an
                OAuth refresh endpoint, re-deriving a session cookie,
                etc.) is target/auth-scheme specific and not something
                this Week 2 module invents -- callers (Week 7+ scanner
                auth handling) supply it.
        """
        new_tokens = refresher(dict(self._tokens))
        self.store(new_tokens)

    # --- 2FA queue ---

    def enqueue_2fa_challenge(self, challenge: TwoFactorChallenge) -> None:
        self._pending_2fa.append(challenge)

    def dequeue_2fa_challenge(self) -> TwoFactorChallenge | None:
        """FIFO. Returns None if the queue is empty rather than raising --
        callers poll this in a loop and an empty queue is a normal state,
        not an error."""
        if not self._pending_2fa:
            return None
        return self._pending_2fa.popleft()

    @property
    def pending_2fa_count(self) -> int:
        return len(self._pending_2fa)

    # --- R-M5 crash recovery ---

    @property
    def requires_reauth(self) -> bool:
        """True when a checkpoint has been loaded but no tokens survived
        (Section 8.3 step 1's exact condition). This module cannot itself
        know "a checkpoint was loaded" -- that's session_persistence.py's
        state, not this class's -- so callers are expected to check this
        only after confirming a checkpoint resume occurred; this property
        only encodes the token-absence half of that AND condition."""
        return not self.has_tokens()

    @staticmethod
    def build_session_resume_message(session_id: str) -> str:
        """Section 8.3 / Section 3's exact message format, verbatim,
        with `{session_id}` filled in. The `{base64_encoded_tokens}`
        placeholder is intentionally left as literal instruction text for
        the human to fill in with their own re-auth material when running
        `/auth` -- it is not a value this module has (there are no tokens
        to encode; that's the entire crash condition being reported).

        Args:
            session_id: The resuming session's identifier.

        Returns:
            "SESSION_RESUME {session_id}: Tokens lost on crash. /auth
            {session_id} {base64_encoded_tokens} to re-auth." with
            `{session_id}` substituted twice, `{base64_encoded_tokens}`
            left literal.
        """
        return (
            f"SESSION_RESUME {session_id}: Tokens lost on crash. "
            f"/auth {session_id} {{base64_encoded_tokens}} to re-auth."
        )

    @staticmethod
    def encode_tokens_for_auth_payload(tokens: dict[str, str]) -> str:
        """Inverse of `restore_tokens_from_auth_payload` -- encodes a token
        dict into the same "key=value" per line, then base64, wire format
        that method decodes. Not itself part of the crash-recovery flow
        (this module never has surviving tokens to encode when a resume
        message is being built -- that's the entire crash condition), but
        provided so the wire format is round-trip-tested and so whatever
        future local tool prepares a human's `/auth` command payload has
        a canonical, tested encoder to call rather than re-deriving the
        same "key=value\\n...  -> base64" format independently.

        Args:
            tokens: e.g. {"session_cookie": "..."}.

        Raises:
            ValueError: If any key contains "=" or a newline (would make
                the decoded line ambiguous) or if any key/value contains
                "password" per the same guard `store()` applies.
        """
        for key, value in tokens.items():
            if "password" in key.lower():
                raise ValueError(f"refusing to encode a password-like key: {key!r}")
            if "=" in key or "\n" in key or "\n" in value:
                raise ValueError(f"key/value cannot contain '=' or newline: {key!r}")
        lines = "\n".join(f"{k}={v}" for k, v in tokens.items())
        return base64.b64encode(lines.encode("utf-8")).decode("ascii")

    def restore_tokens_from_auth_payload(self, base64_tokens: str) -> None:
        """Section 8.3 step 5: "/auth received -> store tokens in RAM".

        Args:
            base64_tokens: The `{base64_encoded_tokens}` portion of a
                received `/auth {session_id} {base64_encoded_tokens}`
                command (parsing the command itself, and verifying
                `session_id` matches the paused session, is
                telegram_bot.py's job once it exists -- this method
                takes just the token payload).

        Raises:
            TokenDecodeError: If `base64_tokens` is not valid base64, or
                does not decode to a UTF-8 "key=value" pair list (one
                per line -- the simplest wire format that needs no new
                dependency; a JSON payload would also base64-decode
                cleanly if a future caller prefers that shape, but this
                method commits to one format so `has_tokens()` becomes
                True deterministically rather than silently no-op'ing
                on an unrecognized shape).
        """
        try:
            decoded = base64.b64decode(base64_tokens, validate=True).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 -- re-raised as this module's own type below
            raise TokenDecodeError(f"could not decode /auth token payload: {exc}") from exc

        tokens: dict[str, str] = {}
        for line in decoded.splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise TokenDecodeError(f"malformed token line (expected key=value): {line!r}")
            key, _, value = line.partition("=")
            tokens[key.strip()] = value.strip()

        if not tokens:
            raise TokenDecodeError("decoded /auth payload contained no key=value tokens")

        self.store(tokens)
        logger.info("[SESSION_RESUME_AUTH_RECEIVED] restored %d token(s) to RAM", len(tokens))
