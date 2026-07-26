"""
Implements: Section 3 -- core/triggers/webhook_trigger.py ("127.0.0.1
binding ENFORCED IN CODE (not just documented)", "server.bind(...) --
verified in preflight").
Also implements: Section 1.4 principle 6 / Layer 1 (Webhook as one of
five trigger sources converging on IntentEngine), Section 8.5's
127.0.0.1-only binding convention (shared with the Go services).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 2 SCOPE:

Parsing approach (documented correction, applied): this module uses
`http.server.BaseHTTPRequestHandler` + `HTTPServer` from the standard
library rather than a hand-rolled parser over raw sockets. This
project's own premise is vulnerabilities arising from HTTP-parsing
DIFFERENTIALS (`http_smuggling_scanner.py`, `services/smuggling_engine/`)
-- writing a bespoke parser for this agent's own inbound listening
surface would be that exact bug class turned against itself. The
stdlib's request-line/header/Content-Length handling is already correct
and battle-tested; there is no reason to re-solve it here.

Documented assumption (single-file, low blast radius -- see
docs/DECISIONS.md, Week 2 section): the blueprint specifies the
127.0.0.1 binding requirement but gives no port number (unlike
race_engine::18080 / smuggling_engine::18081, Section 3), and no
request/response wire contract (path, payload shape, auth). Since a
loopback-only listener is by construction unreachable by any external
bug-bounty platform without a tunnel -- and ngrok/webhook tunnels are a
permanently REJECTED item (Section 13: "No bounty value for solo laptop.
127.0.0.1 only.") -- the only possible caller of this listener is a
local process on the same machine (e.g. a human's curl/test harness
during development, or later, a same-host relay Waild operates). Port
defaults to 8765, overridable via the constructor; this is an arbitrary,
documented choice, not a blueprint citation.

Not built this week: `TriggerRouter` (Section 3's `trigger_router.py`)
does not exist yet -- no Section 12 row before Week 2 builds it, and
Week 2's own line item names only `webhook_trigger.py`. `WebhookTrigger`
exposes `on_event`, a callback hook TriggerRouter will register into
once it exists, rather than importing or duck-typing a TriggerRouter
contract that isn't built yet (same deferral pattern as
docs/DECISIONS.md item 9's token_throttler call-site note).

`WebhookEvent` (below) is a small dataclass scoped to this module's own
HTTP-handoff contract -- not one of Section 3's named ontology types
(core/ontology/helpers.py's Asset/Observation/Hypothesis/AttackStep are
Deep-Lane reasoning concepts, a different scope entirely). Flagged
per the Engineering Constitution's ontology-first rule: if/when
TriggerRouter needs a SHARED event shape across all five trigger
sources (event/schedule/condition/webhook/chat), that shared type likely
belongs in core/ontology/ at that point -- not resolved now since no
other Week 2 component constructs or consumes a WebhookEvent (single
file, no cross-component blast radius).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

logger = logging.getLogger(__name__)

# Section 8.5 / Section 3: "127.0.0.1 binding ENFORCED IN CODE". This is a
# module-level constant, not a constructor parameter -- there is no
# parameter path by which a caller can override it to 0.0.0.0 or any other
# interface. cli/main.py's webhook_binding preflight check (Section 3)
# statically scans this file's source text for a quoted 127.0.0.1
# literal, and for the absence of any quoted all-interfaces wildcard
# address literal (deliberately not spelled out in quotes in this
# comment, to avoid tripping that same scan against this file's own
# explanatory prose).
_BIND_HOST = "127.0.0.1"

# No port is given anywhere in the blueprint for webhook_trigger.py
# (unlike race_engine's 18080 / smuggling_engine's 18081, Section 3).
# Arbitrary, documented default -- see module docstring.
_DEFAULT_PORT = 8765

_MAX_BODY_BYTES = 1_048_576  # 1 MiB: a generous, documented cap so a
# malformed/huge Content-Length on a loopback-only listener can't be used
# to exhaust memory. Not a blueprint number -- this module's own guard.


@dataclass(frozen=True)
class WebhookEvent:
    """One received webhook call. See module docstring re: ontology placement.

    Attributes:
        path: Request path, e.g. "/webhook/hackerone".
        method: HTTP method ("POST", "GET", ...).
        headers: Request headers, lower-cased keys.
        body: Raw request body bytes (empty bytes if none/GET).
    """

    path: str
    method: str
    headers: dict[str, str]
    body: bytes = field(default=b"")


class _WebhookHTTPServer(HTTPServer):
    """HTTPServer subclass carrying the on_event callback to each request
    handler instance via `self.server` (http.server's own convention for
    sharing state between the server and its per-request handlers)."""

    def __init__(self, server_address: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler_cls)
        self.on_event: Callable[[WebhookEvent], None] | None = None


class _WebhookRequestHandler(BaseHTTPRequestHandler):
    """Minimal handler: accepts POST (webhook delivery) and GET (health
    check) on any path, and forwards POSTs to `self.server.on_event` if
    one is registered. Correctness (Content-Length, header parsing,
    keep-alive) is entirely delegated to BaseHTTPRequestHandler / the
    stdlib -- see module docstring on why this project does not hand-roll
    that logic itself.
    """

    server: _WebhookHTTPServer  # narrows the inherited Any-typed attribute

    def do_POST(self) -> None:  # noqa: N802 (http.server's own naming convention)
        length = int(self.headers.get("Content-Length", 0))
        if length > _MAX_BODY_BYTES:
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length) if length else b""
        event = WebhookEvent(
            path=self.path,
            method="POST",
            headers={k.lower(): v for k, v in self.headers.items()},
            body=body,
        )
        if self.server.on_event is not None:
            self.server.on_event(event)
        self.send_response(202)  # accepted; TriggerRouter processes asynchronously once it exists
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        """Health check only -- GET carries no webhook payload semantics."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Routes http.server's default stderr logging through this
        module's logger instead, matching the rest of this codebase's
        logging convention (core/verifier/deterministic_verifier.py)."""
        logger.info("%s - %s", self.address_string(), format % args)


class WebhookTrigger:
    """Section 1.4 Layer 1: the Webhook trigger source. Binds
    `_BIND_HOST` (127.0.0.1) only -- hardcoded, not a constructor
    parameter, so there is no override path to 0.0.0.0 (Section 8.5).

    Runs the blocking `HTTPServer.serve_forever()` loop in a dedicated
    background thread rather than `asyncio.to_thread` -- `to_thread` is
    for a single blocking call that eventually returns; a persistent
    server loop would otherwise occupy a thread-pool-executor thread for
    the entire session. A plain daemon thread is the correct idiom for a
    long-running blocking loop bridged into async code.
    """

    def __init__(self, port: int = _DEFAULT_PORT, on_event: Callable[[WebhookEvent], None] | None = None) -> None:
        """
        Args:
            port: TCP port to listen on. Arbitrary default (module
                docstring) -- no port is specified in the blueprint for
                this trigger. Configurable per-instance for tests and
                for whatever real deployment port is later chosen.
            on_event: Called synchronously from the handler thread for
                every received POST. `None` (default) until
                `TriggerRouter` exists to register a real handler
                (docs/DECISIONS.md, Week 2 section).
        """
        self._port = port
        self._on_event = on_event
        self._server: _WebhookHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def bind_host(self) -> str:
        """Always '127.0.0.1' -- exposed read-only for tests/inspection,
        never settable (Section 8.5's hard requirement)."""
        return _BIND_HOST

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Binds `_BIND_HOST`:port and starts serving in a background thread.

        Raises:
            RuntimeError: If already running.
            OSError: If the port is already in use (propagated from
                `HTTPServer`'s own bind() call -- not swallowed, since a
                silent bind failure here would be a scope/safety-relevant
                surprise, not just an inconvenience).
        """
        if self.is_running:
            raise RuntimeError("WebhookTrigger is already running")
        self._server = _WebhookHTTPServer((_BIND_HOST, self._port), _WebhookRequestHandler)
        self._server.on_event = self._on_event
        self._thread = threading.Thread(target=self._server.serve_forever, name="webhook-trigger", daemon=True)
        self._thread.start()
        logger.info("[WEBHOOK_TRIGGER_STARTED] listening on %s:%d", _BIND_HOST, self._port)

    def stop(self, timeout: float = 5.0) -> None:
        """Stops the server and joins the background thread.

        Args:
            timeout: Max seconds to wait for the thread to exit.
        """
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._server = None
        self._thread = None
        logger.info("[WEBHOOK_TRIGGER_STOPPED]")

    def set_on_event(self, on_event: Callable[[WebhookEvent], None] | None) -> None:
        """Registers/replaces the event callback (e.g. once TriggerRouter
        exists and wires itself in). Safe to call before or after `start()`."""
        self._on_event = on_event
        if self._server is not None:
            self._server.on_event = on_event
