"""
Implements: Section 3 -- core/http/intercepting_client.py ("8 KB body
cap; 100 K entry max; metadata denylist"). Also implements Section 10.5
in full (`_should_suppress_body`, `BODY_CAP_BYTES`, `ENTRY_CAP`,
`WARN_THRESHOLD`, `METADATA_SUPPRESS_PATHS`, large-body disk offload),
and docs/DECISIONS.md items 52 (R-H3's Content-Type-aware body
extension) and the case-fold correction the advisor flagged on top of it.
Blueprint: bb_agent_v6.6_final_blueprint.md

`_should_suppress_body`'s THIRD (body) BRANCH IS THIS SESSION'S ADDITION
ON TOP OF SECTION 10.5's LITERAL TEXT, PER ITEM 52 -- not itself in the
blueprint. Section 10.5's own code only ever checks `request_path` and
the query string. Item 52 (this week's advisor-approved pre-Week-5
scoping) extended it to also check the request body, branching on
Content-Type: `application/x-www-form-urlencoded` bodies reuse the
identical `parse_qs()`-based decode-then-check already used for query
strings (so percent-encoded payloads are caught, not just literal
substrings); everything else (JSON, plain text, unrecognized/absent
Content-Type) falls back to a raw substring scan, since JSON string
values aren't percent-encoded in practice and a scan can't fail to
parse the way a recursive JSON walk could on a malformed body.
`content_type` matching is case-INsensitive on both sides
(`.lower()` applied to the header value and to the literal it's
compared against) -- a real gap the advisor caught by testing
`"Application/X-WWW-Form-Urlencoded"` against the first draft, which
fell through to the substring branch and missed the same payload it
should have caught.

Base64/other non-percent encodings of a metadata URL inside a body
still evade this function -- a documented, conscious limitation of this
one layer (item 52): `_should_suppress_body` decides whether to
SUPPRESS LOGGING of an already-permitted response, it is not the
primary SSRF gate (`scope_enforcer.py` and the scanner-level verifiers,
e.g. `ssrf_verifier.py`, are). Under-detection here means an
already-permitted response gets logged when ideally it wouldn't -- a
telemetry miss, not a bypassed attack -- which is why chasing every
encoding scheme in this one layer isn't the right place to spend
further effort.

TRAFFIC LOG STORE DEFAULTS OPEN, NOT CLOSED -- A DELIBERATE CONTRAST
WITH `CheckpointStore`/`ApprovalManagerProtocol` (docs/DECISIONS.md,
Week 5 section): those two fail closed (raise) when no backing store is
supplied, because a missing checkpoint or a missing approval gate is
itself a safety-relevant absence. Traffic logging is an observability
concern, not a safety gate -- failing the underlying HTTP call (which
scanners need the real response from, to detect anything at all)
because Redis isn't configured in this environment would be a strictly
worse failure mode than just not persisting the log durably. This
module defaults to `InMemoryTrafficLogStore` when no `TrafficLogStore`
is supplied, and the request still completes either way.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from core.ontology.http import TrafficEntry

logger = logging.getLogger(__name__)


# Section 10.5, transcribed verbatim.
BODY_CAP_BYTES = 8_192
ENTRY_CAP = 100_000
WARN_THRESHOLD = 0.80  # log [INTERCEPT_OVERFLOW] at 80% capacity

METADATA_SUPPRESS_PATHS = frozenset(
    {
        "/latest/api/token",
        "/latest/meta-data/",
        "/computeMetadata/v1/",
        "/metadata/instance",
    }
)


def _should_suppress_body(
    request_path: str,
    full_request_url: str,
    request_body: str | bytes | None = None,
    content_type: str | None = None,
) -> bool:
    """Section 10.5 + R-H3 + item 52's Content-Type-aware body extension.

    Args:
        request_path: The request's URL path (e.g. "/api/fetch").
        full_request_url: The complete request URL, including query
            string.
        request_body: The raw request body, if any. `None` or empty for
            bodyless requests (e.g. most `GET`s).
        content_type: The request's `Content-Type` header value, if any.
            Used only to decide which body-parsing branch applies (see
            module docstring); matched case-insensitively.

    Returns:
        `True` if the response body for this request should be
        suppressed from logging.
    """
    # 1. Direct metadata path (agent made a direct metadata call)
    if any(request_path.startswith(p) for p in METADATA_SUPPRESS_PATHS):
        return True

    # 2. SSRF: metadata URL injected as a query parameter value
    parsed = urllib.parse.urlparse(full_request_url)
    for values in urllib.parse.parse_qs(parsed.query).values():
        for v in values:
            if any(v.startswith(p) or p in v for p in METADATA_SUPPRESS_PATHS):
                return True

    # 3. SSRF: metadata URL injected in the request body (item 52).
    if request_body:
        body_text = request_body.decode("utf-8", errors="ignore") if isinstance(request_body, bytes) else request_body
        if content_type is not None and "application/x-www-form-urlencoded" in content_type.lower():
            # Same shape as a query string, just delivered in the body --
            # reuse the identical parse_qs()-based decode-then-check so
            # percent-encoding is handled the same way in both places.
            for values in urllib.parse.parse_qs(body_text).values():
                for v in values:
                    if any(v.startswith(p) or p in v for p in METADATA_SUPPRESS_PATHS):
                        return True
        else:
            # JSON, plain text, or unrecognized/absent content-type: a
            # raw substring scan. JSON string values are not
            # percent-encoded in practice, so this single pass also
            # catches nested JSON without a recursive parser.
            if any(p in body_text for p in METADATA_SUPPRESS_PATHS):
                return True

    return False


class TrafficLogStore(Protocol):
    """Minimal interface a Redis-backed traffic log (Section 8.2: "Redis:
    Working memory, session-scoped. InterceptingClient traffic logs.")
    must satisfy. No Redis connection exists in this environment
    (docs/DECISIONS.md item 7: `redis_connection` fails closed here);
    `InMemoryTrafficLogStore` below is the default, not a stand-in
    waiting to be swapped for something mandatory -- see module
    docstring on why this component defaults open rather than failing
    closed.
    """

    def log_entry(self, entry: TrafficEntry) -> None:
        """Records one traffic entry."""
        ...

    @property
    def entry_count(self) -> int:
        """Current number of entries held."""
        ...


@dataclass
class InMemoryTrafficLogStore:
    """Default `TrafficLogStore`: a ring buffer capped at `max_entries`.

    `collections.deque(maxlen=...)` gives FIFO eviction of the oldest
    entry once the cap is reached "for free" -- Section 10.5 names
    `ENTRY_CAP` as a hard number InterceptingClient enforces, not just
    tracks; this is the built-in Python structure that enforces it
    without hand-written eviction logic. Redis's own `maxmemory-policy
    allkeys-lru` (Section 10.5) is a separate, additional eviction
    mechanism at the real store's memory-pressure layer, not modeled
    here -- this in-memory default has no memory-pressure concept of its
    own beyond the entry count.

    Args:
        max_entries: Ring-buffer capacity. Defaults to `ENTRY_CAP`
            (100,000, Section 10.5's real number). Overridable
            (production code never needs to) so this class's own tests
            can exercise cap/eviction behavior at a small, fast scale
            instead of needing tens of thousands of real entries to
            prove the identical logic.
    """

    max_entries: int = ENTRY_CAP
    _entries: deque[TrafficEntry] = field(init=False)

    def __post_init__(self) -> None:
        self._entries = deque(maxlen=self.max_entries)

    def log_entry(self, entry: TrafficEntry) -> None:
        self._entries.append(entry)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[TrafficEntry]:
        """All currently-held entries, oldest first. Not part of the
        `TrafficLogStore` Protocol (a Redis-backed store would expose
        entries differently) -- provided here for test/debug access to
        this specific implementation."""
        return list(self._entries)


class InterceptingClient:
    """Section 3/10.5's InterceptingClient: makes HTTP requests, applies
    the metadata-suppression and body-size rules to what gets logged,
    and offloads oversized bodies to disk.

    Args:
        transport: An `httpx.AsyncBaseTransport` to use instead of real
            network I/O. Not blueprint-specified -- standard httpx
            dependency injection, used by this module's own tests via
            `httpx.MockTransport` rather than inventing a parallel fake-
            HTTP abstraction.
        store: A `TrafficLogStore`. Defaults to `InMemoryTrafficLogStore`
            (see module docstring on why this defaults open).
        telemetry_dir: Directory large (> `BODY_CAP_BYTES`), non-
            suppressed bodies are written to. Defaults to
            `data/telemetry/large_bodies` (Section 3's own path),
            created on first use if absent.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        store: TrafficLogStore | None = None,
        telemetry_dir: str | Path | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._store = store if store is not None else InMemoryTrafficLogStore()
        self._telemetry_dir = Path(telemetry_dir) if telemetry_dir is not None else Path("data/telemetry/large_bodies")
        self._overflow_warned = False

    @property
    def store(self) -> TrafficLogStore:
        """The traffic log store in use (for tests/inspection)."""
        return self._store

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: str | bytes | None = None,
    ) -> httpx.Response:
        """Makes one HTTP request and records a `TrafficEntry` for it.

        Args:
            method: HTTP method (e.g. "GET", "POST").
            url: The request URL.
            headers: Request headers. `Content-Type` (if present) is
                used only to select `_should_suppress_body`'s body-
                parsing branch.
            content: Request body, if any.

        Returns:
            The `httpx.Response`, unmodified -- suppression only affects
            what gets logged, never what the caller receives.
        """
        response = await self._client.request(method, url, headers=headers, content=content)
        self._record(method, url, response, content, (headers or {}).get("Content-Type"))
        return response

    def _record(
        self,
        method: str,
        url: str,
        response: httpx.Response,
        request_body: str | bytes | None,
        content_type: str | None,
    ) -> None:
        path = urllib.parse.urlparse(url).path
        suppressed = _should_suppress_body(path, url, request_body, content_type)

        body_preview: str | None = None
        body_sha256: str | None = None
        if not suppressed:
            raw = response.content
            if len(raw) <= BODY_CAP_BYTES:
                body_preview = response.text
            else:
                body_sha256 = hashlib.sha256(raw).hexdigest()
                self._write_large_body(body_sha256, raw)

        entry = TrafficEntry(
            method=method,
            url=url,
            status_code=response.status_code,
            headers=dict(response.headers),
            body_preview=body_preview,
            body_sha256=body_sha256,
            suppressed=suppressed,
            timestamp=datetime.now(timezone.utc),
        )
        self._store.log_entry(entry)
        self._check_overflow()

    def _write_large_body(self, digest: str, raw: bytes) -> None:
        self._telemetry_dir.mkdir(parents=True, exist_ok=True)
        (self._telemetry_dir / digest).write_bytes(raw)

    def _check_overflow(self) -> None:
        """Logs `[INTERCEPT_OVERFLOW]` once, the first time the store
        reaches `WARN_THRESHOLD` (80%) of `ENTRY_CAP` -- once, not on
        every subsequent entry, to surface the warning without
        spamming the log for the rest of the session. Not itself
        blueprint-specified ("log once" vs. "log every time past
        threshold"); this is the documented reading.
        """
        if not self._overflow_warned and self._store.entry_count >= WARN_THRESHOLD * ENTRY_CAP:
            logger.warning(
                "[INTERCEPT_OVERFLOW] traffic log at %d/%d entries (%.0f%% of cap)",
                self._store.entry_count,
                ENTRY_CAP,
                WARN_THRESHOLD * 100,
            )
            self._overflow_warned = True

    async def aclose(self) -> None:
        """Closes the underlying `httpx.AsyncClient`."""
        await self._client.aclose()

    async def __aenter__(self) -> InterceptingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
