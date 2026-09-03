"""
Implements: test coverage for core/scanners/jwt_scanner.py (Section 7.13).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import json

import httpx
import jwt
import pytest

from core.http.intercepting_client import InterceptingClient
from core.http.rate_limited_client import RateLimitedClient
from core.scanners.jwt_scanner import JWTScanner, _find_jwt, _load_payloads
from core.scanners.registry import SCANNER_REGISTRY, create_scanner


def _session(handler) -> RateLimitedClient:
    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        scope_domains=["example.com"],
        caller_id="jwt_scanner",
        intercepting_client=InterceptingClient(transport=transport),
    )


_ALG_NONE_ONLY = [{"id": "alg_none_strip_signature", "technique": "alg_none"}]
_WEAK_SECRET_ONLY = [{"id": "weak_secret_literal_secret", "technique": "weak_secret", "secret": "secret"}]


class TestFindJwt:
    def test_finds_jwt_in_json_body(self):
        token = jwt.encode({"sub": "u1"}, "s", algorithm="HS256")
        response = httpx.Response(200, text=json.dumps({"access_token": token}))
        assert _find_jwt(response) == token

    def test_finds_jwt_in_set_cookie(self):
        token = jwt.encode({"sub": "u1"}, "s", algorithm="HS256")
        response = httpx.Response(200, headers={"Set-Cookie": f"session={token}; Path=/"}, text="ok")
        assert _find_jwt(response) == token

    def test_non_jwt_dotted_text_is_rejected(self):
        response = httpx.Response(200, text="version 1.2.3 release notes")
        assert _find_jwt(response) is None

    def test_no_jwt_at_all_returns_none(self):
        response = httpx.Response(200, text="plain page, nothing here")
        assert _find_jwt(response) is None


class TestLoadPayloads:
    def test_real_payload_file_has_alg_none_and_five_weak_secrets(self):
        payloads = _load_payloads()
        assert sum(1 for p in payloads if p["technique"] == "alg_none") == 1
        weak_secrets = [p["secret"] for p in payloads if p["technique"] == "weak_secret"]
        assert len(weak_secrets) == 5
        assert "your-256-bit-secret" in weak_secrets  # jwt.io's own default

    def test_no_rs256_hs256_confusion_entries(self):
        """See module docstring -- a real, flagged gap."""
        payloads = _load_payloads()
        assert not any("rs256" in p["technique"].lower() for p in payloads)


class TestJWTScannerRegistration:
    def test_registered_under_jwt_scanner(self):
        assert SCANNER_REGISTRY["jwt_scanner"] is JWTScanner


class TestJWTScannerPrecondition:
    @pytest.mark.asyncio
    async def test_no_jwt_found_returns_empty(self):
        def handler(request):
            return httpx.Response(401, text="no token here")

        scanner = JWTScanner(_session(handler), payloads=_ALG_NONE_ONLY)
        candidates = await scanner.scan("https://example.com/api/profile")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_baseline_not_401_or_403_returns_empty(self):
        """Nothing to bypass if the endpoint isn't gated in the first
        place -- see module docstring's 'REPLAY TARGET' note."""
        token = jwt.encode({"sub": "u1"}, "secret", algorithm="HS256")

        def handler(request):
            return httpx.Response(200, text=json.dumps({"access_token": token}))

        scanner = JWTScanner(_session(handler), payloads=_ALG_NONE_ONLY)
        candidates = await scanner.scan("https://example.com/api/profile")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_no_payloads_returns_empty(self):
        def handler(request):
            return httpx.Response(401, text="denied")

        scanner = JWTScanner(_session(handler), payloads=[])
        candidates = await scanner.scan("https://example.com/api/profile")
        assert candidates == []


class TestJWTScannerAlgNone:
    @pytest.mark.asyncio
    async def test_alg_none_accepted_produces_candidate(self):
        real_secret = "real-secret-the-scanner-never-sees"
        token = jwt.encode({"sub": "u1", "role": "user"}, real_secret, algorithm="HS256")

        def handler(request):
            auth = request.headers.get("authorization", "")
            if not auth:
                return httpx.Response(401, text=json.dumps({"access_token": token}))
            forged = auth.removeprefix("Bearer ")
            try:
                header = jwt.get_unverified_header(forged)
            except jwt.exceptions.DecodeError:
                return httpx.Response(401, text="invalid")
            # Simulate a vulnerable server that accepts alg:none.
            if header.get("alg") == "none":
                return httpx.Response(200, text="welcome, forged access granted")
            return httpx.Response(401, text="rejected")

        scanner = JWTScanner(_session(handler), payloads=_ALG_NONE_ONLY)
        candidates = await scanner.scan("https://example.com/api/profile")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.vuln_type == "jwt"
        assert candidate.parameter is None
        assert candidate.detected_by == "jwt_scanner"
        assert candidate.probe_correlation_id is None
        assert "alg_none" in candidate.payload_used

    @pytest.mark.asyncio
    async def test_alg_none_rejected_produces_no_candidate(self):
        real_secret = "real-secret"
        token = jwt.encode({"sub": "u1"}, real_secret, algorithm="HS256")

        def handler(request):
            auth = request.headers.get("authorization", "")
            if not auth:
                return httpx.Response(401, text=json.dumps({"access_token": token}))
            return httpx.Response(401, text="rejected")  # server correctly rejects alg:none

        scanner = JWTScanner(_session(handler), payloads=_ALG_NONE_ONLY)
        candidates = await scanner.scan("https://example.com/api/profile")
        assert candidates == []


class TestJWTScannerWeakSecret:
    @pytest.mark.asyncio
    async def test_matching_weak_secret_produces_candidate_and_stops_guessing(self):
        real_secret = "secret"  # matches the one entry in _WEAK_SECRET_ONLY
        token = jwt.encode({"sub": "u1"}, real_secret, algorithm="HS256")
        attempts = []

        def handler(request):
            auth = request.headers.get("authorization", "")
            if not auth:
                return httpx.Response(401, text=json.dumps({"access_token": token}))
            forged = auth.removeprefix("Bearer ")
            attempts.append(forged)
            try:
                jwt.decode(forged, real_secret, algorithms=["HS256"])
                return httpx.Response(200, text="welcome")
            except jwt.exceptions.InvalidSignatureError:
                return httpx.Response(401, text="rejected")

        scanner = JWTScanner(_session(handler), payloads=_WEAK_SECRET_ONLY)
        candidates = await scanner.scan("https://example.com/api/profile")

        assert len(candidates) == 1
        assert "weak_secret:secret" in candidates[0].payload_used
        assert len(attempts) == 1  # exactly one guess attempted, the one that worked

    @pytest.mark.asyncio
    async def test_non_matching_weak_secrets_produce_no_candidate(self):
        real_secret = "actually-quite-strong-and-unguessable"
        token = jwt.encode({"sub": "u1"}, real_secret, algorithm="HS256")

        def handler(request):
            auth = request.headers.get("authorization", "")
            if not auth:
                return httpx.Response(401, text=json.dumps({"access_token": token}))
            forged = auth.removeprefix("Bearer ")
            try:
                jwt.decode(forged, real_secret, algorithms=["HS256"])
                return httpx.Response(200, text="welcome")
            except jwt.exceptions.InvalidSignatureError:
                return httpx.Response(401, text="rejected")

        scanner = JWTScanner(_session(handler), payloads=_WEAK_SECRET_ONLY)
        candidates = await scanner.scan("https://example.com/api/profile")
        assert candidates == []

    @pytest.mark.asyncio
    async def test_weak_secret_not_attempted_when_original_alg_is_not_hs256(self):
        """See module docstring -- weak_secret is gated on the
        discovered token's own alg being HS256."""
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode({"sub": "u1"}, private_key, algorithm="RS256")
        attempts = []

        def handler(request):
            auth = request.headers.get("authorization", "")
            if not auth:
                return httpx.Response(401, text=json.dumps({"access_token": token}))
            attempts.append(auth)
            return httpx.Response(401, text="rejected")

        scanner = JWTScanner(_session(handler), payloads=_WEAK_SECRET_ONLY)
        await scanner.scan("https://example.com/api/profile")
        assert attempts == []  # never even attempted -- alg was RS256, not HS256


class TestJWTScannerCandidateFields:
    @pytest.mark.asyncio
    async def test_raw_response_snapshot_truncated_to_512_chars(self):
        real_secret = "s"
        token = jwt.encode({"sub": "u1"}, real_secret, algorithm="HS256")

        def handler(request):
            auth = request.headers.get("authorization", "")
            if not auth:
                return httpx.Response(401, text=json.dumps({"access_token": token}))
            header = jwt.get_unverified_header(auth.removeprefix("Bearer "))
            if header.get("alg") == "none":
                return httpx.Response(200, text="L" * 2000)
            return httpx.Response(401, text="rejected")

        scanner = JWTScanner(_session(handler), payloads=_ALG_NONE_ONLY)
        candidates = await scanner.scan("https://example.com/api/profile")
        assert len(candidates) == 1
        assert len(candidates[0].raw_response_snapshot) == 512


class TestJWTScannerViaCreateScanner:
    def test_create_scanner_works(self):
        scanner = create_scanner("jwt_scanner", scope_domains=["example.com"])
        assert isinstance(scanner, JWTScanner)
