"""
Implements: Section 4.2/4.3 -- InteractshClient
Blueprint: bb_agent_v6.6_final_blueprint.md

FILE LOCATION -- `core/http/interactsh_client.py`, AUTHORED, NOT
BLUEPRINT-ASSIGNED (docs/DECISIONS.md item 81): Section 4.2 gives this
class a name and a 5-step docstring but never a file path -- unlike
every scanner (Section 3's file tree) or `ExploitCandidate`/
`EndpointSignals` (which at least got a home named, even if their fields
didn't -- items 69/70), `InteractshClient` has no Section 3 tree entry
at all. `core/knowledge/` was explicitly considered and ruled out: it
does not exist yet (confirmed before writing a line of this file), and
its four named residents (`rag_engine.py`, `knowledge_indexer.py`,
`provenance.py`, `payload_engine.py`) are RAG/embedding/payload-template
concerns, not networking -- no thematic fit. `core/governance/` was also
considered and ruled out: policy/enforcement (`scope_enforcer.py`,
`safety_gate.py`), not a client. `core/http/` is the chosen home: this
class IS an HTTP client polling an external service, sibling to the two
files already there (`intercepting_client.py`, `rate_limited_client.py`)
-- no new top-level directory is warranted when an existing, thematically
correct one already exists.

THREE THINGS THIS FILE DELIBERATELY DOES NOT IMPLEMENT, EACH ONE
PLUGGABLE RATHER THAN GUESSED -- consistent with `scripts/
interactsh_setup.py`'s own already-established precedent (Week 0,
predates this file) for exactly the same shape of gap:

  1. THE SELF-HOSTED GO SUBPROCESS ITSELF. `core/control/
     process_supervisor.py` ("Go service lifecycle management," Section
     3) does not exist yet (confirmed before writing this file) --
     nothing in this codebase manages a Go subprocess's lifecycle at
     all, and no self-hosted interactsh Go source exists anywhere in
     this repository. `interactsh_setup.py`'s own `try_self_hosted_
     fallback` already established the pattern: an injected starter
     callable, defaulting to `None`, with "no launcher available" and
     "launcher failed" both correctly cascading to UNAVAILABLE rather
     than silently pretending a subprocess started. `InteractshClient`
     below (`_self_hosted_start_fn`) follows the identical pattern for
     the identical reason.

  2. THE REAL INTERACTSH WIRE PROTOCOL. Section 4.2's 5-step docstring
     names correlation-ID generation, polling, and a timeout -- it does
     not mention the real interactsh service's actual protocol (a
     public-key registration handshake, then polling an endpoint that
     returns encrypted interaction logs decrypted with the matching
     private key). Implementing that from nothing, un-cited, would be
     exactly the silent invention the Engineering Constitution's STOP
     CONDITIONS forbid -- worse than authoring a plausible field list,
     since a wrong crypto protocol assumption fails silently (looks like
     "no interaction," not an error) rather than loudly. `_poll_check_fn`
     is pluggable; the shipped default (`_default_poll_check`) is
     explicitly labeled a placeholder -- one GET request, any non-empty
     2xx body counts as received -- sufficient to exercise the state
     machine end to end in tests and against a stub target, not a claim
     of real interactsh compatibility.

  3. RESULT-CONFIRMATION SEMANTICS BEYOND "did a request arrive." What a
     received interaction actually PROVES for a given probe (correlating
     it back to a specific `ExploitCandidate`, feeding Section 5.1's
     `oob_interaction` evidence type into `EvidenceChain`) is
     Verification-layer work -- out of scope here, same boundary
     `xss_scanner.py`/`sqli_scanner.py` already drew against
     `xss_verifier.py`/`sqli_verifier.py` (items 75/76).

WHAT THIS FILE DOES IMPLEMENT FOR REAL, NOT PLUGGABLY: correlation ID
generation (Section 4.2 step 1, verbatim), the 15s/60s poll schedule and
5-minute timeout (Section 4.2 steps 4-5, verbatim), and Section 4.3's
full failure cascade as a real, tested state machine (429x3 -> self-
hosted attempt; self-hosted attempt fails -> UNAVAILABLE; network
partition mid-poll -> ENV_DEPENDENT). These are the parts Section 4.2/
4.3 actually specify, and nothing here is deferred that has a concrete
blueprint answer.

`RateLimitedClient` NEEDS NO CODE CHANGE FOR THIS (a real gap, found and
resolved without touching it): `docs/DECISIONS.md` item 66 already
flagged that `RateLimitedClient.request()` calls `is_allowed()` (scope-
only), never `is_allowed_outbound()` (item 71's interactsh/metadata
exception) -- meaning a `RateLimitedClient` built with a real target's
`scope_domains` cannot reach `*.interactsh.com` at all. Retrofitting
`RateLimitedClient` globally to always grant that exception was
considered and rejected, for the exact reason item 66 already gave:
"a scanner's own HTTP calls should not silently gain an SSRF-adjacent
exception it never asked for." The actual fix needed here is much
narrower than that: `InteractshClient` takes its own, DEDICATED
`RateLimitedClient` (constructor-injected, per this file's established
DI convention), which its caller is expected to construct with
`scope_domains=["*.interactsh.com"]` -- `_is_scope_allowed`'s EXISTING,
UNCHANGED wildcard logic (Section 4.4, Week 3) already grants this
correctly with zero code changes anywhere, because the "scope" being
checked is this dedicated client's own, separate from whatever real
target `scope_domains` a scanner's own `RateLimitedClient` instance is
built with. Every EXISTING `RateLimitedClient` caller's behavior is
completely unaffected.

SESSION-LEVEL, NOT PER-SCANNER (authored interpretation): Section 4.2's
`correlation_id = f"XBOW_{session_id}_{nonce}"` names a `session_id`,
not a per-scanner ID -- `session_id` is already an established, opaque
`str` parameter elsewhere in this codebase (`credential_lifecycle.py`,
`session_persistence.py`), owned by session-level orchestration, not
generated by any single component. Read together, this implies ONE
`InteractshClient` per BB-Agent session, shared across every scanner
that needs OOB confirmation (Batch 2: SSRF, CMDi, XXE, Deserialization,
Host Header) -- each calls `register_probe()` for its own fresh
correlation ID against the same client, rather than each constructing
its own client. Not yet wired to real orchestration (that does not exist
yet); this file is the client itself, ready to be shared once it is.

TWO FURTHER AUTHORED INTERPRETATIONS, NEITHER DIRECTLY BLUEPRINT-
SPECIFIED: (1) the 429 counter is cumulative across the client's whole
PUBLIC-mode lifetime, not reset per probe -- "the public service is
rate-limiting us" is a session-wide condition about the SERVICE, not a
property of any one probe. Resets to zero on any non-429 response,
matching "consecutive." (2) mode transitions are one-directional for the
session (PUBLIC -> SELF_HOSTED -> UNAVAILABLE, never back) -- Section
4.3 gives no recovery/cooldown rule for this state machine, unlike
`adaptive_strategy.py`'s explicit 300-second cooldown for a different
state machine; absence of a stated recovery path means none is
implemented, not that one is invented to match a different section's
unrelated pattern.

CONSEQUENCES OF DEGRADATION (Section 4.3: "Degrade: in-band SSRF only;
CMDi/XXE/Deser skip OOB phase") ARE NOT THIS FILE'S JOB: those are
per-scanner behavioral decisions (Batch 2, not yet built) made by
checking `InteractshClient.mode`, not something this client enforces or
implements itself. This file's job ends at accurately tracking and
reporting `mode`.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Awaitable, Callable

import httpx

from core.governance.scope_enforcer import INTERACTSH_SUFFIX
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.enums import InteractshMode, OOBPollOutcome

POLL_INTERVAL_EARLY_SECONDS = 15  # Section 4.2 step 4: "every 15s for first 2 min"
POLL_INTERVAL_LATE_SECONDS = 60  # Section 4.2 step 4: "every 60s for min 3-5"
EARLY_PHASE_DURATION_SECONDS = 120  # "first 2 min"
TOTAL_TIMEOUT_SECONDS = 300  # Section 4.2 step 5: "Timeout 5 min"
PUBLIC_CONSECUTIVE_429_THRESHOLD = 3  # Section 4.3: "Public 429 x 3"; matches
# scripts/interactsh_setup.py's PUBLIC_RATE_LIMIT_CONSECUTIVE_429 value
# (not imported from there -- that constant belongs to a one-shot Week 0
# preflight check, a different lifecycle from this session-long client;
# duplicating one `int` literal with a citation is not the "same type,
# two owners" problem items 71/81 both resolved by consolidating).


class InteractshRateLimited(Exception):
    """Raised by a poll-check implementation on HTTP 429. Caught inside
    `InteractshClient.poll()` -- never expected to propagate out of it."""


async def _default_poll_check(session: RateLimitedClient, correlation_id: str) -> bool:
    """AUTHORED PLACEHOLDER -- see module docstring, point 2. One GET
    request; a non-empty 2xx body counts as a received interaction.

    HOSTNAME CASE IS NOT PRESERVED HERE, AND THAT IS CORRECT, NOT A BUG:
    embedding `correlation_id` in the request's hostname means httpx
    lowercases it during URL construction (RFC 3986: hostnames are
    case-insensitive) -- confirmed by a test that first asserted exact-
    case preservation and failed against a real (non-mocked-away) URL
    build, not assumed. This placeholder's own "any non-empty body"
    check never compares `correlation_id` against anything, so the
    normalization has no effect here. Left as a note for whoever
    replaces this with the real protocol (module docstring, point 2):
    if exact-case correlation-ID matching ever matters, it belongs in a
    query parameter or path segment of the CHECK request, not relied on
    via the hostname -- the `oob_url` embedded in the actual probe
    payload sent to a target (`register_probe()`'s return value) is
    unaffected either way, since that string is never itself passed
    through an HTTP client's URL parser.

    Args:
        session: The dedicated, interactsh-scoped `RateLimitedClient`.
        correlation_id: This probe's correlation ID.

    Returns:
        `True` if an interaction appears to have been received.

    Raises:
        InteractshRateLimited: On HTTP 429.
    """
    url = f"https://{correlation_id}{INTERACTSH_SUFFIX}/"
    response = await session.request("GET", url)
    if response.status_code == 429:
        raise InteractshRateLimited(f"429 from {url}")
    return response.status_code == 200 and bool(response.text.strip())


class InteractshClient:
    """Section 4.2/4.3. See module docstring for what is real vs.
    pluggable, and why."""

    def __init__(
        self,
        session: RateLimitedClient,
        *,
        session_id: str,
        self_hosted_start_fn: Callable[[], bool] | None = None,
        poll_check_fn: Callable[[RateLimitedClient, str], Awaitable[bool]] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        """
        Args:
            session: A `RateLimitedClient` dedicated to this client --
                see module docstring's "RateLimitedClient needs no code
                change" section for why its caller must construct it
                with `scope_domains=["*.interactsh.com"]`.
            session_id: This BB-Agent session's identifier (module
                docstring: "session-level, not per-scanner").
            self_hosted_start_fn: See module docstring, point 1. `None`
                (default) means no self-hosted mechanism is available --
                a 429x3 self-hosted attempt goes straight to
                `UNAVAILABLE`, matching `interactsh_setup.py`'s own
                established behavior for the identical situation.
            poll_check_fn: See module docstring, point 2. Defaults to
                `_default_poll_check`.
            sleep_fn: Matches `RateLimiter`'s established convention
                (`core/http/rate_limited_client.py`) exactly -- same
                signature, same default (`asyncio.sleep`), same reason
                (deterministic tests, no real wall-clock waits).
            time_fn: See `sleep_fn`. Defaults to `time.monotonic`.
        """
        self.session = session
        self._session_id = session_id
        self._self_hosted_start_fn = self_hosted_start_fn
        self._poll_check_fn = poll_check_fn if poll_check_fn is not None else _default_poll_check
        self._sleep_fn = sleep_fn if sleep_fn is not None else _default_sleep
        self._time_fn = time_fn if time_fn is not None else time.monotonic

        self.mode = InteractshMode.PUBLIC
        self._consecutive_429_count = 0

    def register_probe(self) -> str:
        """Section 4.2 steps 1-2, verbatim: generates a fresh correlation
        ID and returns the callback URL to embed in a probe payload.

        Returns:
            `oob_url`, e.g. `"XBOW_<session_id>_<nonce>.interactsh.com"`.
        """
        nonce = secrets.token_hex(8)
        correlation_id = f"XBOW_{self._session_id}_{nonce}"
        return f"{correlation_id}{INTERACTSH_SUFFIX}"

    async def poll(self, oob_url: str) -> OOBPollOutcome:
        """Section 4.2 steps 4-5 and Section 4.3's failure cascade. See
        module docstring for the full state-machine design.

        Args:
            oob_url: An `oob_url` previously returned by
                `register_probe()`.

        Returns:
            `OOBPollOutcome.RECEIVED` as soon as a callback is detected.
            `OOBPollOutcome.UNAVAILABLE` immediately if `self.mode` is
            already `UNAVAILABLE` (nothing to poll with), or after this
            probe's own 5-minute timeout with no callback.
            `OOBPollOutcome.ENV_DEPENDENT` if a transport-level error
            (not a 429 -- see `InteractshRateLimited`) occurs mid-poll.
        """
        if self.mode is InteractshMode.UNAVAILABLE:
            return OOBPollOutcome.UNAVAILABLE

        correlation_id = oob_url.removesuffix(INTERACTSH_SUFFIX)
        elapsed = 0.0

        while elapsed < TOTAL_TIMEOUT_SECONDS:
            interval = (
                POLL_INTERVAL_EARLY_SECONDS
                if elapsed < EARLY_PHASE_DURATION_SECONDS
                else POLL_INTERVAL_LATE_SECONDS
            )
            await self._sleep_fn(interval)
            elapsed += interval

            if self.mode is InteractshMode.UNAVAILABLE:
                # A prior iteration's 429 handling just exhausted the
                # self-hosted fallback -- stop polling immediately
                # rather than continuing against a mode with nothing
                # left to check.
                return OOBPollOutcome.UNAVAILABLE

            try:
                received = await self._poll_check_fn(self.session, correlation_id)
            except InteractshRateLimited:
                self._handle_429()
                continue
            except httpx.TransportError:
                return OOBPollOutcome.ENV_DEPENDENT

            self._consecutive_429_count = 0  # any clean response resets the streak
            if received:
                return OOBPollOutcome.RECEIVED

        return OOBPollOutcome.UNAVAILABLE

    def _handle_429(self) -> None:
        """Section 4.3: "Public 429 x 3 -> Switch to self-hosted." Only
        meaningful while `self.mode` is `PUBLIC` -- a 429 while already
        `SELF_HOSTED` would mean the injected `poll_check_fn` is
        signaling rate-limiting against a non-public target, which this
        client has no defined response to (Section 4.3 only names the
        public path's 429 behavior); such a signal is silently ignored
        rather than guessed at.
        """
        if self.mode is not InteractshMode.PUBLIC:
            return

        self._consecutive_429_count += 1
        if self._consecutive_429_count < PUBLIC_CONSECUTIVE_429_THRESHOLD:
            return

        if self._self_hosted_start_fn is None:
            self.mode = InteractshMode.UNAVAILABLE
            return

        try:
            started = self._self_hosted_start_fn()
        except Exception:  # noqa: BLE001 -- boundary to an injected,
            # caller-supplied callable; classified into UNAVAILABLE
            # rather than propagating an arbitrary exception type out of
            # poll(), matching interactsh_setup.py's identical boundary
            # decision for the identical situation.
            started = False

        self.mode = InteractshMode.SELF_HOSTED if started else InteractshMode.UNAVAILABLE


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
