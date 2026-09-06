"""
Implements: Section 7.28 -- hardcoded_credentials.py
Blueprint: bb_agent_v6.6_final_blueprint.md

`credential_validation_allowlist` CALLER_ID GATING -- TREATED WITH THE
SAME RIGOR AS THE `interactsh_client` NAMING PIN (Waild's explicit
directive), AND A REAL, PRE-EXISTING INFRASTRUCTURE GAP CLOSED FIRST,
NOT ASSUMED WORKING (docs/DECISIONS.md item 96 has the full trace):
before writing a line of this scanner, traced `is_allowed()`'s
exemption branch (Section 4.4/R-H4) all the way down to
`RateLimitedClient` and found it was NEVER ACTUALLY WIRED THROUGH --
`RateLimitedClient.request()` called `is_allowed()` without passing
`credential_validation_allowlist` at all, so the exemption was
unreachable through it regardless of `caller_id` being correct. Fixed
at the root (`rate_limited_client.py`, item 96) before this file was
written, not worked around here.

THIS SCANNER NEVER TYPES THE LITERAL STRING `"hardcoded_credentials"`
AS A `caller_id` ANYWHERE -- it reads `self.session.caller_id` (already
correct, set by `create_scanner`'s existing `caller_id=scanner_id`
wiring, item 82, as long as this class is registered under exactly that
string) and reuses it verbatim when constructing its own internal
validation client. One source of truth for this scanner's identity --
`SCANNER_REGISTRY`'s own key -- never re-typed as a second literal that
could silently drift from it, the exact class of bug item 22's
29-vs-28 Tier C list already taught this project to guard against.

TWO SESSIONS, DELIBERATELY -- `self.session` (injected, scoped to the
TARGET's domains) fetches the JS being scanned for patterns; a second,
internally-constructed `RateLimitedClient` (`self._validation_session`,
`scope_domains=[]`) makes the external provider-validation calls. Empty
`scope_domains` is a deliberate safety property, not an oversight: with
nothing in normal scope, `is_allowed()`'s first (ordinary scope) check
can never succeed for this client -- it can ONLY ever reach a host via
the `credential_validation_allowlist` exemption, so this client
structurally cannot be misused to reach the target's own domains or any
other out-of-scope host, even by a future bug in this file.

LIVE VALIDATION FOR 2 OF 4 PROVIDERS; MICROSOFT GRAPH HAS NO PATTERN AT
ALL -- INVESTIGATED, NOT GUESSED AT, AND ONE MISTAKE CAUGHT ALONG THE
WAY (docs/DECISIONS.md item 97 has the full trace): `credential_
validation_allowlist.external_apis` names five providers (Section 3's
`scope.yaml` comment). Stripe and Google Maps use a SINGLE bearer key /
query-string key respectively -- a plain GET with that one value either
succeeds or doesn't, safely implementable and verified directly (both
endpoints confirmed GET-only, read-only, matching Section 7.28's "All
calls are GET-only and read-only"). AWS requires SigV4 request signing
AND a *paired* secret access key (a single regex match on an access key
ID alone cannot authenticate anything) -- genuinely separate,
non-trivial infrastructure this project doesn't have. Twilio needs a
paired Account SID + Auth Token located near each other in the same JS,
a different (and not yet built) "find related values together" pattern
this file's simple per-pattern regex scan doesn't attempt. Both are
still surfaced as findings on a plain pattern match -- Section 7.28's
own fallback: "mark finding as TIER_D; send Telegram alert for human to
validate the extracted key manually." `payload_used` says plainly that
validation wasn't attempted and why, since neither TIER_D assignment
nor `TelegramBot` exist yet in this codebase for this scanner to
actually hand off to -- an honest, simplified approximation of that
fallback, not the real thing.

Microsoft Graph got no pattern entry at all, not just no validation --
checked against Microsoft's own current documentation rather than
assumed from memory: its Sensitive Information Type definition
describes only a broad `[-_.~a-zA-Z0-9]` character set (no distinctive
prefix the way AWS/Stripe/Google's keys have), and a gitleaks
maintainer thread (Oct 2024) confirms the wider secret-scanning
community hasn't converged on anything more specific either. An early
draft of `hardcoded_credentials_patterns.json` had a pattern requiring
a literal `"8Q~"` substring -- caught before it shipped: that was
almost certainly a garbled half-memory of Microsoft's own illustrative
example value (`"abc7Q~defgh..."`, from their SIT documentation), not a
real structural signature every secret of this type contains. Rather
than ship a broad, high-false-positive pattern with a vague caveat,
Graph detection is left out entirely, pending a keyword-proximity
heuristic (e.g. requiring a match near `client_secret`) this file
doesn't implement yet.

KNOWN PLACEHOLDER VALUES EXCLUDED PER PROVIDER -- e.g. AWS's own
documented example key (`AKIAIOSFODNN7EXAMPLE`, used throughout AWS's
own public documentation) is excluded so this scanner doesn't flag
harmless, widely-copied documentation examples as findings.

`ExploitCandidate.parameter = None` -- AN EIGHTH EXTENSION TO ITEM 69'S
LIST, FOUND HERE: this scanner doesn't inject anything at all -- it
passively scans fetched JS content for patterns, with no query/body
parameter and no header involved in the technique. No single parameter
in the conventional sense to name, the same underlying shape every
prior extension (XXE, `api_versioning.py`, `jwt_scanner.py`) already
has, arrived at independently for this scanner's own actual mechanics.

`interactsh_client` -- NOT DECLARED: Section 7.28 names no OOB
technique.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.http.rate_limited_client import RateLimitedClient
from core.ontology.findings import ExploitCandidate
from core.ontology.scope import CredentialValidationAllowlist
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PATTERN_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "hardcoded_credentials_patterns.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention


def _load_patterns(path: Path = PATTERN_FILE) -> list[dict]:
    """Loads and returns the `patterns` array from
    `hardcoded_credentials_patterns.json`.

    Note the key is `patterns`, not `payloads` -- this file is
    `pattern_library` type (Section 3.1), a different schema
    convention than every `injectable_payload` file this project has
    used so far.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["patterns"]


@register("hardcoded_credentials")
class HardcodedCredentialsScanner(BaseScanner):
    """Section 7.28. See module docstring for the caller_id gating, the
    two-session design, and the per-provider validation gap."""

    def __init__(
        self,
        session,
        *,
        patterns: list[dict] | None = None,
        credential_validation_allowlist: CredentialValidationAllowlist | None = None,
    ) -> None:
        """
        Args:
            session: See `BaseScanner`. Used to fetch the JS being
                scanned for patterns -- NOT used for external provider
                validation calls (see `_validation_session`).
            patterns: Overrides `_load_patterns()`'s result, for tests.
            credential_validation_allowlist: The `credential_validation_
                allowlist` exemption data (Section 4.4/R-H4), reached
                via `create_scanner`'s `**scanner_kwargs` passthrough
                (item 82), the same mechanism `ssrf_scanner.py` et al.
                use for `interactsh_client`. `None` (default) means no
                live external validation is attempted for any provider
                -- see module docstring.
        """
        super().__init__(session)
        self._patterns = patterns if patterns is not None else _load_patterns()
        self._validation_session = RateLimitedClient(
            scope_domains=[],  # deliberately empty -- see module docstring
            caller_id=self.session.caller_id,  # never re-typed as a fresh literal
            credential_validation_allowlist=credential_validation_allowlist,
        )

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: The JS file URL to scan for credential-shaped
                patterns.

        Returns:
            `[]` if no pattern entries are loaded. Otherwise one
            `ExploitCandidate` per matched, non-placeholder credential
            that either validated successfully (providers with live
            validation configured) or was found via pattern match alone
            (providers without).
        """
        if not self._patterns:
            return []

        response = await self.session.request("GET", target_url)

        candidates: list[ExploitCandidate] = []
        for entry in self._patterns:
            for match in re.finditer(entry["pattern"], response.text):
                key = match.group(0)
                if key in entry.get("known_placeholders", []):
                    continue

                if entry["live_validation"]:
                    validated = await self._validate(entry["provider"], key)
                    if not validated:
                        continue
                    note = f"[{entry['provider']}] live-validated"
                else:
                    note = f"[{entry['provider']}] UNVALIDATED -- {entry['gap_reason']}"

                candidates.append(self._candidate(target_url, key, note, response.text))
        return candidates

    async def _validate(self, provider: str, key: str) -> bool:
        """Dispatches to the provider-specific live-validation check.

        Args:
            provider: One of `"stripe"` or `"google_maps"` -- the only
                two providers with live validation configured (see
                module docstring).
            key: The extracted credential to validate.

        Returns:
            `True` if the provider confirms the key is live.
        """
        if provider == "stripe":
            return await self._validate_stripe(key)
        if provider == "google_maps":
            return await self._validate_google_maps(key)
        return False

    async def _validate_stripe(self, key: str) -> bool:
        response = await self._validation_session.request(
            "GET", "https://api.stripe.com/v1/account", headers={"Authorization": f"Bearer {key}"}
        )
        return response.status_code == 200

    async def _validate_google_maps(self, key: str) -> bool:
        response = await self._validation_session.request(
            "GET", f"https://maps.googleapis.com/maps/api/geocode/json?address=test&key={key}"
        )
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return False
        return isinstance(data, dict) and data.get("status") != "REQUEST_DENIED"

    def _candidate(self, target_url: str, key: str, note: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="hardcoded_credentials",
            endpoint=target_url,
            http_method="GET",
            parameter=None,
            detected_by="hardcoded_credentials",
            payload_used=f"{note}: {key}",
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
