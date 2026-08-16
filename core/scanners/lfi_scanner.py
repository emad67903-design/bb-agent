"""
Implements: Section 7.10 -- lfi_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

TWO-PHASE DESIGN, PER SECTION 7.10'S OWN TEXT:
  1. WINDOWS FINGERPRINT CHECK, FIRST, ALWAYS: "On Windows fingerprint
     (IIS/ASP.NET headers): log [LFI_WINDOWS_DEFERRED]; return 0
     ExploitCandidates" (Section 3's `lfi_scanner.py` comment, verbatim
     condition). One request (the baseline fetch, needed anyway for the
     content heuristic below) supplies the headers this check reads --
     no extra request spent purely on fingerprinting.
  2. LINUX-ONLY TESTING, OTHERWISE: `/etc/hostname` across the 3 payload
     file encodings (`lfi_payloads.json`'s own `_status` note: "Section
     7.10's own text: 'triple confirmation across 3 distinct path
     encodings' -- these are the 3").

FINGERPRINT SIGNATURES ARE AUTHORED, NOT ENUMERATED BY THE BLUEPRINT:
Section 7.10 names the SIGNAL CATEGORY ("IIS/ASP.NET response headers")
but no exact header names or values. `WINDOWS_FINGERPRINT_HEADERS` below
is this scanner's own reasonable, industry-standard reading of that
category (`Server: Microsoft-IIS/...`, `X-Powered-By: ASP.NET`,
`X-AspNet-Version` presence) -- flagged as authored the same way
`sqli_scanner.py`'s `DB_ERROR_SIGNATURES` already is.

HOSTNAME CONTENT CHECK IS SHARED WITH `path_traversal.py` (docs/
DECISIONS.md item 78): `core.scanners.content_heuristics.
looks_like_hostname_content` -- see that module's docstring for the
three-part rule and its inherent limits.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.content_heuristics import looks_like_hostname_content
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "lfi_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

logger = logging.getLogger(__name__)

# Authored, not blueprint-enumerated -- see module docstring.
WINDOWS_FINGERPRINT_HEADERS: tuple[tuple[str, str], ...] = (
    ("server", "iis"),
    ("server", "microsoft-iis"),
    ("x-powered-by", "asp.net"),
)
WINDOWS_FINGERPRINT_PRESENCE_ONLY_HEADERS: tuple[str, ...] = ("x-aspnet-version",)


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `lfi_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _is_windows_fingerprinted(headers) -> bool:
    """See `WINDOWS_FINGERPRINT_HEADERS`/`WINDOWS_FINGERPRINT_PRESENCE_ONLY_HEADERS`.

    Args:
        headers: An `httpx.Headers`-like mapping (case-insensitive get).

    Returns:
        `True` if any known IIS/ASP.NET signal is present.
    """
    for header_name in WINDOWS_FINGERPRINT_PRESENCE_ONLY_HEADERS:
        if headers.get(header_name) is not None:
            return True
    for header_name, expected_substring in WINDOWS_FINGERPRINT_HEADERS:
        value = headers.get(header_name)
        if value is not None and expected_substring in value.lower():
            return True
    return False


@register("lfi_scanner")
class LFIScanner(BaseScanner):
    """Section 7.10. See module docstring for the Windows-deferral and
    Linux-hostname-heuristic design."""

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
            `[]` on Windows fingerprint (matches Section 3's own
            "returns 0 ExploitCandidates" wording) or when `target_url`
            has no query parameters. Otherwise one `ExploitCandidate`
            per (query parameter, encoding) combination whose response
            passes `looks_like_hostname_content`.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []

        baseline = await self.session.request("GET", target_url)

        if _is_windows_fingerprinted(baseline.headers):
            logger.info("[LFI_WINDOWS_DEFERRED] %s -- deferring to path_traversal.py", target_url)
            return []

        candidates: list[ExploitCandidate] = []
        for payload_entry in self._payloads:
            payload = payload_entry["payload"]
            for point in iter_query_param_injections(target_url, payload):
                response = await self.session.request("GET", point.url)
                if looks_like_hostname_content(response.text, baseline.text):
                    candidates.append(
                        ExploitCandidate(
                            vuln_type="lfi",
                            endpoint=target_url,
                            http_method="GET",
                            parameter=point.parameter,
                            detected_by="lfi_scanner",
                            payload_used=payload,
                            raw_response_snapshot=response.text[:RAW_RESPONSE_SNAPSHOT_CHARS],
                        )
                    )
        return candidates
