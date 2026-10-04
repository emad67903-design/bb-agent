"""
Implements: Section 7.21 -- http_smuggling.py
Blueprint: bb_agent_v6.6_final_blueprint.md

GO SIDE DOES THE RAW SOCKET WORK; THIS FILE BUILDS THE BYTES AND READS
THE VERDICT -- MATCHING smuggling.go's OWN DIVISION OF LABOR, NOT
ASSUMED: services/smuggling_engine/smuggling.go's header comment states
this explicitly ("Go only performs raw socket I/O against the already-
built bytes"). `handleSmuggle` opens a raw TCP (or TLS) connection
directly to `target_url`'s host:port, writes each variant's decoded raw
bytes, waits up to 10s for a read, and reports `timed_out`/`elapsed_ms`
per variant plus a pre-computed `vulnerable` bool (`results[0].TimedOut
!= results[1].TimedOut`). This scanner never parses target response
bytes itself -- it reads the Go service's own verdict.

TWO-TIER PAYLOAD FILES, ONE PER THIS SCANNER'S OWN TWO JOBS -- MATCHING
EACH FILE'S OWN `_notes` FIELD, NOT AN ARBITRARY SPLIT: Section 3.1
lists `smuggling_configs.json` ("CL vs TE variants") and
`http_smuggling_payloads.json` ("Raw-byte variants") as two separate
files for this one scanner, unlike every other scanner's single file.
`smuggling_configs.json` declares WHICH variant types exist (just
"CL.TE"/"TE.CL", matching `smuggling.go`'s own `type` field);
`http_smuggling_payloads.json` holds the raw byte TEMPLATE for each.
Both are consulted every scan; neither alone is sufficient.

`payload_engine.py` DELIBERATELY BYPASSED, NOT WAITED ON -- A
DOCUMENTED JUDGMENT CALL, NARROW TO THIS FILE: `smuggling.go`'s own
header comment names `payload_engine.py` as the intended builder of the
raw bytes ("Python (payload_engine.py, an injectable_payload consumer
per Section 3.1) builds the raw bytes..."). `payload_engine.py` does
not exist in this repository (confirmed: `find . -iname
payload_engine.py` returns nothing) and is not in Batch 5's scope --
its blueprint contract ("renders (template, context) -> injectable
string... LLM selects payload by INDEX; PCFG applies transforms") is a
general-purpose engine meant to serve many scanners, not a one-off
dependency to stand up for two fixed byte templates. This file builds
the two raw requests directly, with a plain `str.format(path=..,
host=..)` call against `http_smuggling_payloads.json`'s templates --
functionally the same outcome `payload_engine.py` would eventually
produce for this narrow case, without inventing the general machinery.
Revisit this file's `_build_raw_request` if/when `payload_engine.py` is
actually built.

BYTE TEMPLATES VERIFIED AGAINST PORTSWIGGER'S OWN PUBLISHED TECHNIQUE,
NOT INVENTED: see `http_smuggling_payloads.json`'s own `_status` field
for the byte-level proof (each template's body length checked against
its own declared `Content-Length` before being stored). Source:
https://portswigger.net/web-security/request-smuggling/finding,
"Finding CL.TE/TE.CL vulnerabilities using timing techniques".

HOW THIS SCANNER REACHES ITS OWN GO SIDECAR -- A NEW, DOCUMENTED
INFRASTRUCTURE DECISION (docs/DECISIONS.md item 103), NOT INVENTED
SILENTLY HERE: `self.session` (the scanner's `RateLimitedClient`) is
scoped to the bug-bounty program's own `scope_domains` -- calling
`127.0.0.1:18081` through it would raise `OutOfScopeError`
(`scope_enforcer.is_allowed` has no localhost carve-out; confirmed by
reading its full source before writing this file). `127.0.0.1` is also
not "the target" in the sense Section 10.2's scope enforcement protects
against -- it is this agent's own sidecar process, which independently
re-validates `target_url` against ITS OWN `scope_guard.go` before
touching the network (Section 10.2's fourth, independent layer). This
scanner therefore constructs a SECOND `RateLimitedClient` instance
(`go_service_client`), scoped only to `127.0.0.1`, for the one local
hop -- still "the one HTTP layer" (Engineering Constitution), still
auditable/rate-limited, still passes `ci-scanner-http-check` (no direct
`httpx`/`requests` import appears in this file). Injectable via the
constructor, defaulted to a real instance when not supplied, matching
the `payloads`-argument shape Batch 1 already established -- not the
`interactsh_client`-argument shape, since that parameter's `None`
default means "skip this technique"; a missing Go-service client here
is not optional degradation, it is how this scanner's one technique
works at all. `race_scanner.py` (deferred, pre-investigation only this
batch) will need the identical mechanism when it is written.

`ExploitCandidate.parameter = None` -- THE 10TH EXTENSION TO ITEM 69'S
LIST (docs/DECISIONS.md item 102 confirms the count stood at 9 before
this scanner; item 104 records this as the 10th): Section 7.21's
signal is a connection-level timing differential between two raw
requests sent to a host:port -- there is no query parameter, body
parameter, or even a single header whose value is "the injected thing"
the way, say, `host_header.py`'s `Host` header is. Both raw requests
ARE the payload, in their entirety; `payload_used` records which
variant types were sent instead.

`http_method = "POST"` -- both raw templates are POST requests (a
smuggling probe needs a request body to desync the two parsers'
chunk/length framing against each other; GET's typically-absent body
gives nothing to desync). Matches the templates themselves, not
asserted independently of them.

`interactsh_client` -- NOT DECLARED, NOT AN OMISSION: Section 7.21
names no OOB technique (its own evidence line is `timing_anomaly +
differential = 2`, not `oob_interaction`). Same precedent as
`crlf_injection.py` (item 88) and every other non-OOB scanner --
`interactsh_client: InteractshClient | None = None` was pinned (item
83) specifically for the five scanners that need it, not retrofitted
onto every scanner regardless of need.

GO-SERVICE-ERROR HANDLING, A NEW, DOCUMENTED PATTERN FOR THIS FIRST
GO-BACKED SCANNER: a non-200 from the sidecar (e.g. the Go service's
own `scope_guard.go` independently rejecting `target_url`, which would
indicate a scope-configuration mismatch between this agent's own
`scope_domains` and the Go service's `-scope-json` file) is logged
(`[HTTP_SMUGGLING_GO_SERVICE_ERROR]`) and degrades to `[]`, the same
"log a bracketed tag, return no candidates" shape
`host_header.py`'s OOB-unavailable path already established --
not raised, since a configuration mismatch at the infrastructure layer
is not itself evidence of (or against) the vulnerability this scanner
tests for. No `try/except` around the request itself: a hard connection
failure to the sidecar (e.g. the Go service not running at all)
propagates like every other scanner's `self.session.request()` call
already does -- not a new failure-handling shape invented for this one
case.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

from core.http.rate_limited_client import RateLimitedClient
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "smuggling_configs.json"
PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "http_smuggling_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

# Section 8.5: "Binding: 127.0.0.1 only -- never 0.0.0.0." Port 18081 is
# smuggling_engine's own fixed port (Section 3's file-tree annotation for
# services/smuggling_engine/main.go) -- not a scope.yaml-configurable value,
# unlike race_scanner.py's race_parallel (R-L7 fix); nothing in the blueprint
# makes this port configurable, so it is a named constant, not a YAML key.
SMUGGLING_ENGINE_HOST = "127.0.0.1"
SMUGGLING_ENGINE_URL = "http://127.0.0.1:18081/smuggle"

logger = logging.getLogger(__name__)


def _load_configs(path: Path = CONFIG_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `smuggling_configs.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `http_smuggling_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _default_go_service_client() -> RateLimitedClient:
    """Builds the default `RateLimitedClient` used to reach smuggling_engine.

    See module docstring's "HOW THIS SCANNER REACHES ITS OWN GO SIDECAR"
    note. `requests_per_second=100.0`: this is a loopback call to the
    agent's own sidecar process, not a politeness limit against a real
    target -- Section 10.3's `10 req/sec/host` default governs target
    traffic, not local infrastructure calls.
    """
    return RateLimitedClient(
        scope_domains=[SMUGGLING_ENGINE_HOST],
        caller_id="http_smuggling",
        requests_per_second=100.0,
    )


def _split_host_and_path(target_url: str) -> tuple[str, str]:
    """Splits `target_url` into a `Host`-header value and a request-line path.

    Args:
        target_url: An absolute URL, e.g. `"https://example.com/api/x"`.

    Returns:
        `(host, path)`. `host` includes `:port` only when the port is
        non-default for the URL's scheme (matching normal client
        behavior -- an explicit `:443` on an `https://` URL would be
        redundant and some servers treat it as a distinct virtual
        host). `path` is `"?"`-joined with the query string when
        present, and defaults to `"/"` when `target_url` has no path
        (e.g. `"https://example.com"`).
    """
    parsed = urlsplit(target_url)
    default_port = 443 if parsed.scheme == "https" else 80
    if parsed.port is not None and parsed.port != default_port:
        host = f"{parsed.hostname}:{parsed.port}"
    else:
        host = parsed.hostname
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return host, path


@register("http_smuggling")
class HTTPSmugglingScanner(BaseScanner):
    """Section 7.21. See module docstring for the Go-sidecar mechanism,
    the payload_engine.py bypass, and the parameter=None 10th-extension
    note."""

    def __init__(
        self,
        session,
        *,
        configs: list[dict] | None = None,
        payloads: list[dict] | None = None,
        go_service_client: RateLimitedClient | None = None,
    ) -> None:
        """
        Args:
            session: See `BaseScanner`. Unused by this scanner's own
                network call (see module docstring) but still required
                -- every scanner's `__init__` takes `session` first,
                matching `base_scanner.py`'s one shared contract.
            configs: See `xss_scanner.XSSScanner`'s `payloads` argument
                for the injectable/production-default shape. Defaults
                to `smuggling_configs.json`'s real content.
            payloads: Same shape, for `http_smuggling_payloads.json`.
            go_service_client: The `RateLimitedClient` used to reach
                `smuggling_engine` at `127.0.0.1:18081`. Defaults to a
                real, usable client (`_default_go_service_client`) when
                not supplied -- unlike `interactsh_client`, `None` here
                is a constructor convenience, not a runtime "OOB
                unavailable, degrade" signal; this scanner always has a
                working client, injected or default.
        """
        super().__init__(session)
        self._configs = configs if configs is not None else _load_configs()
        payload_entries = payloads if payloads is not None else _load_payloads()
        self._payload_templates: dict[str, str] = {p["type"]: p["raw_request_template"] for p in payload_entries}
        self._go_service_client = go_service_client if go_service_client is not None else _default_go_service_client()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            `[]` if no config/payload entries are loaded, if fewer than
            2 variants can be built (a config entry whose `type` has no
            matching payload template is skipped, not fatal), or if the
            Go service reports `vulnerable: false` (or is unreachable
            in-scope-wise -- see module docstring's Go-service-error
            note). Otherwise exactly one `ExploitCandidate` -- Section
            7.21's evidence (`timing_anomaly + differential = 2`) is a
            single combined signal, not one per variant.
        """
        if not self._configs or not self._payload_templates:
            return []

        host, path = _split_host_and_path(target_url)
        variants = self._build_variants(host, path)
        if len(variants) < 2:
            return []

        response = await self._go_service_client.request(
            "POST",
            SMUGGLING_ENGINE_URL,
            content=json.dumps({"target_url": target_url, "variants": variants}),
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            logger.warning(
                "[HTTP_SMUGGLING_GO_SERVICE_ERROR] %s -- smuggling_engine returned status=%d",
                target_url,
                response.status_code,
            )
            return []

        result = json.loads(response.text)
        if not result.get("vulnerable"):
            return []

        return [self._candidate(target_url, variants, response.text)]

    def _build_variants(self, host: str, path: str) -> list[dict]:
        """Renders each configured variant's raw request and base64-encodes
        it, per `smuggling.go`'s `raw_request_b64` wire-contract field.

        A config entry whose `type` has no matching payload template is
        silently skipped (not every `smuggling_configs.json` entry is
        guaranteed a template in a hand-edited payload file) -- `scan`'s
        own `len(variants) < 2` check is what actually enforces the Go
        service's 2-variant requirement.
        """
        variants: list[dict] = []
        for config in self._configs:
            variant_type = config["type"]
            template = self._payload_templates.get(variant_type)
            if template is None:
                continue
            raw_request = template.format(path=path, host=host)
            variants.append(
                {
                    "type": variant_type,
                    "raw_request_b64": base64.b64encode(raw_request.encode("utf-8")).decode("ascii"),
                }
            )
        return variants

    def _candidate(self, target_url: str, variants: list[dict], response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="http_smuggling",
            endpoint=target_url,
            http_method="POST",
            parameter=None,
            detected_by="http_smuggling",
            payload_used="; ".join(v["type"] for v in variants),
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
