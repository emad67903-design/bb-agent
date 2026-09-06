"""
Implements: Section 7.3 -- ssrf_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

TWO INDEPENDENT DETECTION PATHS, BOTH RUN, PER SECTION 7.3'S OWN TEXT:
  1. IN-BAND MULTI-CLOUD METADATA PROBE MATRIX -- Priorities 3/4/5 only
     (see "PRIORITIES 1+2 DELIBERATELY NOT IMPLEMENTED" below). Each
     candidate query parameter is tried against AWS IMDSv1 (401 =
     IMDS_V2_ENFORCED), GCP (`computeMetadata`/PERMISSION_DENIED body
     markers), and Azure (`"compute"`/`"network"` JSON-key markers). No
     interactsh involved -- the target's own response is read directly.
  2. OOB CALLBACK -- Section 7.3: "OOB detection: Interactsh callback."
     A fresh correlation ID per candidate parameter (see "ONE
     `oob_url` PER PARAMETER" below), probes sent, then polled
     concurrently.

`interactsh_client: InteractshClient | None = None` -- EXACT NAME,
PINNED (Waild's item-82 approval message, citing item 22's list-drift
precedent): this is the first of Batch 2's five scanners; the other
four (cmd_injection, xxe, deserialization, host_header) declare this
identically. `None` means the OOB path degrades per Section 4.3
("Degrade: in-band SSRF only") -- `_scan_oob` returns `[]` immediately,
logging `[SSRF_OOB_UNAVAILABLE]`; the in-band metadata path is
unaffected, since it never touches `interactsh_client` at all.

PRIORITIES 1+2 DELIBERATELY NOT IMPLEMENTED -- Section 7.3's own
"Implementation note on Priorities 1+2": "Standard URL-injection SSRF
... produces the IMDS_V2_ENFORCED signal (Priority 3) in all modern AWS
environments. Full IMDSv2 token extraction requires both SSRF AND
method/header injection -- document as a chain-level finding ... Do not
document Priority 1+2 as the default path for simple URL-injection
SSRF." This scanner tests exactly one thing per candidate: substituting
a payload into an existing query parameter (`param_injection.py`'s
established, GET-query-param-only scope, same limitation Batch 1
already accepted). It has no method-override or header-injection
capability of its own, so Priority 1 (PUT `/latest/api/token`) and
Priority 2 (GET with that token) are outside what THIS scanner can
achieve alone; correlating a separate header-injection finding with an
SSRF finding into the Priority-1+2 chain is Deep Lane/ChainEngine's job
(Section 6.7), not Fast Lane's. Priority 3 is "always achievable" per
Section 7.3's own table; that is what is implemented.

IS_ALLOWED_OUTBOUND / RATE_LIMITED_CLIENT -- CHECKED, NOT ASSUMED (docs/
DECISIONS.md item 83 has the full trace): `scope_enforcer.
is_allowed_outbound()`'s own docstring (item 71) names "Week 7's CMDi/
XXE/Deserialization/SSRF scanners ... via RateLimitedClient" as an
intended caller. Traced through this scanner's actual request pattern
before writing a line of it: every metadata/OOB URL is embedded as a
QUERY-PARAMETER VALUE delivered to `target_url` (already in
`scope_domains`) -- never a destination `self.session` itself connects
to. 169.254.169.254 is link-local (RFC 3927): it only resolves to real
cloud metadata from INSIDE the target's own VM network, which is the
entire point of SSRF (the vulnerable TARGET makes that request, not
this scanner). OOB polling goes through `InteractshClient`'s own
dedicated session (item 81), never `self.session`. Conclusion:
`RateLimitedClient` needs no change; `is_allowed()` (scope-only, its
current behavior) is correct as-is for this scanner. Item 71's
anticipation does not materialize -- closed here with reasoning, not
silently left open.

ONE `oob_url` PER PARAMETER, NOT ONE SHARED ACROSS ALL OF THEM:
`ExploitCandidate.parameter` is a required field for SSRF (not one of
item 69's four no-parameter cases), and a callback alone does not say
which parameter caused it -- so each candidate parameter gets its own
`register_probe()` call and its own correlation ID, not a single ID
reused across every parameter on the endpoint.

CONCURRENT POLLING IS AN AUTHORED EFFICIENCY DECISION, NOT BLUEPRINT-
CITED: `InteractshClient.poll()` blocks up to 5 real minutes per call
(Section 4.2 step 5). Section 7.3 does not discuss concurrency across
multiple candidate parameters on one endpoint. Polling them
sequentially would cost up to N x 5 minutes for one `scan()` call on
one endpoint, against Section 6.6's 1-3 hour Fast Lane budget for all
29 scanners combined. `_scan_oob` sends every probe first, then polls
all of them together via `asyncio.gather` -- each `poll()` call is
still independently scoped to its own correlation ID, so this changes
wall-clock cost only, not detection semantics.

`probe_correlation_id` HOLDS THE BARE CORRELATION ID, NOT THE FULL
`oob_url`: Section 4.2's own notation distinguishes `correlation_id`
("XBOW_{session_id}_{nonce}") from `oob_url` ("{correlation_id}.
interactsh.com") as two different strings. `InteractshClient.poll()`
itself derives the bare ID via `oob_url.removesuffix(INTERACTSH_SUFFIX)`
internally (interactsh_client.py) -- this scanner reuses that exact
same derivation (same imported constant) when populating
`ExploitCandidate.probe_correlation_id`, rather than storing the full
hostname or inventing a different stripping method.

AWS'S 401 CHECK GETS A BASELINE GUARD; GCP/AZURE'S BODY-MARKER CHECKS
DO NOT -- asymmetric on purpose. HTTP 401 is a generic status code an
auth-walled endpoint could already return for every request regardless
of any SSRF payload; Section 7.3's own table already anticipates this
implicitly by calling 401 a *signal* rather than proof on its own. The
GCP/Azure markers (`computeMetadata`, the specific PERMISSION_DENIED
JSON shape, the quoted `"compute"`/`"network"` JSON keys) are specific
enough that Section 7.3 does not ask for a baseline comparison, and
adding one un-requested risks a false negative on the exact narrow
signal named. A single baseline fetch (once per `scan()` call, same
one-baseline-per-scan convention as `lfi_scanner.py`/`sqli_scanner.py`)
backs the AWS check only.

DETECTION MARKERS ARE AUTHORED, NOT BLUEPRINT-ENUMERATED (see
`_matches_gcp_signal`/`_matches_azure_signal` below) -- same "authored,
not blueprint-enumerated" flag `sqli_scanner.py`'s `DB_ERROR_SIGNATURES`
and `lfi_scanner.py`'s `WINDOWS_FINGERPRINT_HEADERS` already carry.
Section 7.3 names the signal in prose; the exact substring checks are
this scanner's own literal reading of that prose.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from core.governance.scope_enforcer import INTERACTSH_SUFFIX
from core.http.interactsh_client import InteractshClient
from core.ontology.enums import InteractshMode, OOBPollOutcome
from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "ssrf_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

logger = logging.getLogger(__name__)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `ssrf_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _matches_gcp_signal(body: str) -> bool:
    """Section 7.3 Priority 4 -- see module docstring's "DETECTION
    MARKERS ARE AUTHORED" note.

    Args:
        body: The target's response body for a GCP metadata probe.

    Returns:
        `True` if `body` contains the `computeMetadata` substring, or
        the GCP PERMISSION_DENIED error JSON's two markers together
        (checked together, not "permission_denied" alone, so a generic
        unrelated access-denied page doesn't false-positive).
    """
    lowered = body.lower()
    if "computemetadata" in lowered:
        return True
    return "status" in lowered and "permission_denied" in lowered


def _matches_azure_signal(body: str) -> bool:
    """Section 7.3 Priority 5 -- see module docstring's "DETECTION
    MARKERS ARE AUTHORED" note.

    Args:
        body: The target's response body for an Azure metadata probe.

    Returns:
        `True` if `body` contains `"compute"` or `"network"` as a
        quoted JSON key (quotes included in the check, so plain-English
        use of either word outside a JSON-key context doesn't
        false-positive).
    """
    lowered = body.lower()
    return '"compute"' in lowered or '"network"' in lowered


@register("ssrf_scanner")
class SSRFScanner(BaseScanner):
    """Section 7.3. See module docstring for the in-band/OOB split, the
    Priority 1+2 exclusion, and the `is_allowed_outbound` trace."""

    def __init__(
        self,
        session,
        *,
        payloads: list[dict] | None = None,
        interactsh_client: InteractshClient | None = None,
    ) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`.
            interactsh_client: Shared, session-level OOB client (item 81),
                reached via `create_scanner`'s `**scanner_kwargs`
                passthrough (item 82). `None` (default) degrades to
                in-band-only per Section 4.3 -- see module docstring.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()
        self._interactsh_client = interactsh_client

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            `[]` if `target_url` has no query parameters or no payload
            entries are loaded. Otherwise the concatenation of the
            in-band metadata path's and the OOB path's candidates.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []

        candidates: list[ExploitCandidate] = []
        candidates.extend(await self._scan_in_band_metadata(target_url))
        candidates.extend(await self._scan_oob(target_url))
        return candidates

    async def _scan_in_band_metadata(self, target_url: str) -> list[ExploitCandidate]:
        """Priorities 3/4/5 -- see module docstring."""
        metadata_entries = [p for p in self._payloads if p["technique"] == "in_band_metadata"]
        if not metadata_entries:
            return []

        param_names = [pt.parameter for pt in iter_query_param_injections(target_url, "_probe")]
        baseline = await self.session.request("GET", target_url)

        candidates: list[ExploitCandidate] = []
        for param_name in param_names:
            for entry in metadata_entries:
                payload = entry["payload"]
                response = await self._probe(target_url, param_name, payload)

                if entry["cloud"] == "aws":
                    # Baseline-guarded -- see module docstring's
                    # "AWS'S 401 CHECK GETS A BASELINE GUARD" note.
                    if response.status_code == 401 and baseline.status_code != 401:
                        candidates.append(self._candidate(target_url, param_name, payload, response.text))
                elif entry["cloud"] == "gcp":
                    if _matches_gcp_signal(response.text):
                        candidates.append(self._candidate(target_url, param_name, payload, response.text))
                elif entry["cloud"] == "azure":
                    if _matches_azure_signal(response.text):
                        candidates.append(self._candidate(target_url, param_name, payload, response.text))
        return candidates

    async def _scan_oob(self, target_url: str) -> list[ExploitCandidate]:
        """OOB callback path -- see module docstring's "ONE `oob_url`
        PER PARAMETER" and "CONCURRENT POLLING" notes."""
        if self._interactsh_client is None or self._interactsh_client.mode is InteractshMode.UNAVAILABLE:
            logger.info(
                "[SSRF_OOB_UNAVAILABLE] %s -- OOB phase skipped (Section 4.3: degrade to in-band only)",
                target_url,
            )
            return []

        oob_entries = [p for p in self._payloads if p["technique"] == "oob"]
        if not oob_entries:
            return []

        param_names = [pt.parameter for pt in iter_query_param_injections(target_url, "_probe")]

        probes: list[tuple[str, str, str]] = []  # (parameter, oob_url, payload)
        for param_name in param_names:
            oob_url = self._interactsh_client.register_probe()
            payload = oob_entries[0]["payload_template"].format(oob_url=oob_url)
            probes.append((param_name, oob_url, payload))

        response_texts: dict[str, str] = {}
        for param_name, _oob_url, payload in probes:
            response = await self._probe(target_url, param_name, payload)
            response_texts[param_name] = response.text

        outcomes = await asyncio.gather(*(self._interactsh_client.poll(oob_url) for _p, oob_url, _pl in probes))

        candidates: list[ExploitCandidate] = []
        for (param_name, oob_url, payload), outcome in zip(probes, outcomes):
            if outcome is OOBPollOutcome.RECEIVED:
                correlation_id = oob_url.removesuffix(INTERACTSH_SUFFIX)
                candidates.append(
                    self._candidate(
                        target_url,
                        param_name,
                        payload,
                        response_texts[param_name],
                        probe_correlation_id=correlation_id,
                    )
                )
        return candidates

    async def _probe(self, target_url: str, param_name: str, payload: str):
        """Sends `payload` as `param_name`'s value on `target_url`.

        Args:
            target_url: The endpoint under test.
            param_name: Which existing query parameter to substitute.
            payload: The literal value to substitute in.

        Returns:
            The `httpx.Response` from the target.
        """
        points = iter_query_param_injections(target_url, payload)
        point = next(pt for pt in points if pt.parameter == param_name)
        return await self.session.request("GET", point.url)

    def _candidate(
        self,
        target_url: str,
        parameter: str,
        payload: str,
        response_text: str,
        *,
        probe_correlation_id: str | None = None,
    ) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="ssrf",
            endpoint=target_url,
            http_method="GET",
            parameter=parameter,
            detected_by="ssrf_scanner",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=probe_correlation_id,
        )
