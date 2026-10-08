"""
Implements: Section 7.5 -- race_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

GO SIDE DOES THE CONCURRENT TRANSPORT WORK; THIS FILE BUILDS THE SINGLE
REQUEST SHAPE AND READS THE VERDICT -- MATCHING race.go's OWN DIVISION
OF LABOR, CONFIRMED (docs/DECISIONS.md item 2), NOT ASSUMED:
`services/race_engine/race.go` fires `parallel` concurrent copies of one
caller-supplied request (method/headers/body) against `target_url`,
classifies each as successful via a caller-supplied
`success_status_codes` allowlist, and returns `success_count`/`total`
plus per-request nanosecond timestamps. This scanner never decides which
responses "count" as successful redemptions beyond that status-code
allowlist -- the full `expected-vs-vulnerable` interpretation item 2
reserved for `race_scanner.py` is exactly the one job this file does:
compare the Go service's own `success_count` against Section 7.5's
documented baseline (`_EXPECTED_SUCCESS_COUNT = 1`).

ONE PAYLOAD ENTRY, NOT A PER-VARIANT SET -- MATCHING SECTION 7.5'S OWN
DETECTION SHAPE, NOT AN ARBITRARY SIMPLIFICATION FROM
`http_smuggling.py`'s TWO-VARIANT PRECEDENT: Section 7.5 fires "N
concurrent requests" -- one burst of one request repeated, not several
different request shapes each tried N times. `data/payloads/
race_payloads.json` therefore holds exactly one template (`method`,
`headers`, `body`, `success_status_codes`), read directly rather than
hardcoded, keeping the request shape config-driven (Engineering
Constitution: "zero magic numbers") without inventing a multi-variant
structure this scanner's technique does not call for.

`race_parallel` SOURCED FROM `scope.yaml`, NOT `config.py` -- R-L7 FIX,
NOT A LOCAL CHOICE: Section 3's own file-tree comment for this scanner
("race_parallel: reads from scope_config.race_parallel... source of
truth = scope.yaml; NOT config.py") and `core/config.py`'s own matching
note are both explicit. `core/governance/scope_config_generator.py`'s
own docstring states it "stays the single place that knows how to read
configs/scope.yaml" -- so this constructor's production default calls
that module's `load_race_parallel()` (added alongside this file, docs/
DECISIONS.md item 106) rather than reading `configs/scope.yaml` a
second, parallel way here.

HOW THIS SCANNER REACHES ITS OWN GO SIDECAR -- THE ALREADY-DOCUMENTED
INFRASTRUCTURE DECISION (docs/DECISIONS.md item 103), REUSED VERBATIM,
NOT RE-DERIVED: `self.session` (this scanner's `RateLimitedClient`) is
scoped to the bug-bounty program's own `scope_domains` and has no
`127.0.0.1` carve-out (`scope_enforcer.is_allowed`, confirmed by its
full source, same check `http_smuggling.py` already made). This scanner
therefore constructs a SECOND `RateLimitedClient` instance
(`go_service_client`), scoped only to `127.0.0.1`, for the one local hop
to `race_engine:18080` -- `http_smuggling.py` (item 104) already proved
this exact mechanism working end-to-end for its own sidecar at
`:18081`; item 105's pre-investigation named this file as the next,
already-anticipated consumer. Still "the one HTTP layer" (Engineering
Constitution): no direct `httpx`/`requests` import appears in this file.

`ExploitCandidate.parameter = None` -- THE 11TH EXTENSION TO ITEM 69'S
LIST (docs/DECISIONS.md item 104 confirms the count stood at 10 after
`http_smuggling.py`; this entry is item 106's own count, recorded in
DECISIONS.md, not silently assumed here): Section 7.5's signal is
"success_count > 1" across N concurrent copies of one whole replayed
request -- there is no single query parameter, body parameter, or
header value that "is" the race condition the way, say,
`host_header.py`'s `Host` header is. The entire replayed request, fired
`race_parallel` times, is the payload; `payload_used` records this
template's own `id` instead.

`http_method` -- READ FROM THE PAYLOAD TEMPLATE, NOT HARDCODED: unlike
`http_smuggling.py` (whose `POST` is fixed because its own two raw byte
templates are both literally POST requests, with no field to read it
from), `race_payloads.json`'s one entry carries an explicit `method`
field. Reading it keeps the method config-driven rather than a second,
possibly-drifting Python-side assertion of what the JSON already states.

`interactsh_client` -- NOT DECLARED, NOT AN OMISSION: Section 7.5 names
no OOB technique (Section 5.3's achievability matrix marks Race's `oob`
column [UNAVAILABLE]; its own evidence line is `differential +
timing_anomaly + (replay_stable OR timing_confirmed)`, never
`oob_interaction`). Same precedent as `http_smuggling.py` (item 104)
and `crlf_injection.py` (item 88).

EVIDENCE-TYPE CLASSIFICATION IS DELIBERATELY NOT THIS FILE'S JOB:
Section 7.5 names three evidence types for a confirmed race
(`differential`, `timing_anomaly`, and a `replay_stable`/
`timing_confirmed` choice), but deciding which of those apply to a given
candidate is `core/verifier/evidence_chain.py`'s `build_evidence_chain()`
job (Week 7's later verifier-layer deliverable, not yet built), exactly
as `core/ontology/findings.py`'s own `EvidenceChain.min_required`
docstring already establishes ("NOT looked up lazily inside this
dataclass... resolves and injects it at construction time"). This
scanner's job, like every other Fast Lane scanner, is only to decide
WHETHER a signal exists and to populate `ExploitCandidate.success_count`/
`total_requests` with the raw numbers that later layer will need --
not to pre-compute `timing_confirmed`'s own separate `>= 3` threshold
(Section 7.5) itself. That threshold belongs with the component that
actually builds `EvidenceChain.collected_types`, not here.

GO-SERVICE-ERROR HANDLING -- SAME PATTERN `http_smuggling.py` ALREADY
ESTABLISHED (item 104), REUSED, NOT REINVENTED: a non-200 from
`race_engine` (e.g. a scope-configuration mismatch between this agent's
`scope_domains` and the Go service's own `-scope-json` file) is logged
(`[RACE_GO_SERVICE_ERROR]`) and degrades to `[]` -- not raised, since an
infrastructure-layer mismatch is not itself evidence for or against the
vulnerability. A hard connection failure to the sidecar itself is NOT
caught here and propagates normally, matching every other scanner's
unwrapped `self.session.request()` call (this scanner's own call is
unwrapped too -- see `go_service_client.request()` below).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.governance.scope_config_generator import load_race_parallel
from core.http.rate_limited_client import RateLimitedClient
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "race_payloads.json"
SCOPE_YAML_FILE = Path(__file__).resolve().parent.parent.parent / "configs" / "scope.yaml"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

# Section 8.5: "Binding: 127.0.0.1 only -- never 0.0.0.0." Port 18080 is
# race_engine's own fixed port (Section 3's file-tree annotation for
# services/race_engine/main.go) -- not itself a scope.yaml-configurable
# value (unlike race_parallel, R-L7 fix); nothing in the blueprint makes
# the PORT configurable, so it is a named constant, not a YAML key.
RACE_ENGINE_HOST = "127.0.0.1"
RACE_ENGINE_URL = "http://127.0.0.1:18080/race"

# Section 7.5: "Safe exploit: Concurrent coupon redemption on test
# account. Expected: 1 success. Vulnerable: >= 2." A candidate is
# emitted only when the Go service's own success_count exceeds this.
_EXPECTED_SUCCESS_COUNT = 1

logger = logging.getLogger(__name__)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `race_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _default_go_service_client() -> RateLimitedClient:
    """Builds the default `RateLimitedClient` used to reach race_engine.

    See module docstring's "HOW THIS SCANNER REACHES ITS OWN GO SIDECAR"
    note. `requests_per_second=100.0`: this is a loopback call to the
    agent's own sidecar process, not a politeness limit against a real
    target -- Section 10.3's `10 req/sec/host` default governs target
    traffic, not local infrastructure calls (same reasoning, same
    number, as `http_smuggling.py`'s identical default).
    """
    return RateLimitedClient(
        scope_domains=[RACE_ENGINE_HOST],
        caller_id="race_scanner",
        requests_per_second=100.0,
    )


@register("race_scanner")
class RaceScanner(BaseScanner):
    """Section 7.5. See module docstring for the Go-sidecar mechanism,
    the single-template detection shape, and the parameter=None
    11th-extension note."""

    def __init__(
        self,
        session,
        *,
        payloads: list[dict] | None = None,
        race_parallel: int | None = None,
        go_service_client: RateLimitedClient | None = None,
    ) -> None:
        """
        Args:
            session: See `BaseScanner`. Unused by this scanner's own
                network call (see module docstring) but still required
                -- every scanner's `__init__` takes `session` first,
                matching `base_scanner.py`'s one shared contract.
            payloads: See `xss_scanner.XSSScanner`'s `payloads` argument
                for the injectable/production-default shape. Defaults to
                `race_payloads.json`'s real content. An empty list is a
                legal (if degenerate) value -- `scan` returns `[]`
                without calling the Go service, the same "config present
                but empty, degrade quietly" shape `http_smuggling.py`
                established for its own empty-configs/payloads case.
            race_parallel: The concurrency to request from race_engine.
                Defaults to `load_race_parallel(configs/scope.yaml)` when
                not supplied -- the R-L7 fix's authoritative source,
                read via the one module that knows how to read
                `scope.yaml` (see module docstring). Test-injectable so
                tests never depend on a real `scope.yaml` file existing
                at the expected path.
            go_service_client: The `RateLimitedClient` used to reach
                `race_engine` at `127.0.0.1:18080`. Defaults to a real,
                usable client (`_default_go_service_client`) when not
                supplied -- same "always has a working client, injected
                or default" convention `http_smuggling.py`'s identical
                parameter already established, unlike `interactsh_client`
                (whose `None` default means "skip this technique").
        """
        super().__init__(session)
        payload_entries = payloads if payloads is not None else _load_payloads()
        self._payload: dict | None = payload_entries[0] if payload_entries else None
        self._race_parallel = race_parallel if race_parallel is not None else load_race_parallel(SCOPE_YAML_FILE)
        self._go_service_client = go_service_client if go_service_client is not None else _default_go_service_client()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            `[]` if no payload entry is loaded, if the Go service
            reports a non-200 (see module docstring's Go-service-error
            note), or if `success_count` does not exceed Section 7.5's
            `_EXPECTED_SUCCESS_COUNT` baseline. Otherwise exactly one
            `ExploitCandidate` -- one race probe (one burst of
            `race_parallel` concurrent copies of one request) is a
            single combined signal, not one candidate per request.
        """
        if self._payload is None:
            return []

        request_body = {
            "target_url": target_url,
            "method": self._payload["method"],
            "headers": self._payload["headers"],
            "body": self._payload["body"],
            "parallel": self._race_parallel,
            "success_status_codes": self._payload["success_status_codes"],
        }
        response = await self._go_service_client.request(
            "POST",
            RACE_ENGINE_URL,
            content=json.dumps(request_body),
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            logger.warning(
                "[RACE_GO_SERVICE_ERROR] %s -- race_engine returned status=%d",
                target_url,
                response.status_code,
            )
            return []

        result = json.loads(response.text)
        success_count = result["success_count"]
        total = result["total"]

        if success_count <= _EXPECTED_SUCCESS_COUNT:
            return []

        return [self._candidate(target_url, success_count, total, response.text)]

    def _candidate(self, target_url: str, success_count: int, total: int, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="race_scanner",
            endpoint=target_url,
            http_method=self._payload["method"],
            parameter=None,
            detected_by="race_scanner",
            payload_used=self._payload["id"],
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
            success_count=success_count,
            total_requests=total,
        )
