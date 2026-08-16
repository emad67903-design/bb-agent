"""
Implements: Section 7.18 -- path_traversal.py
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE -- LINUX + WINDOWS ONLY, "ZIP AND API VARIANTS" DEFERRED, NOT
GUESSED: Section 7.18's Detection line names four things: "Linux:
../../../etc/hostname. Windows: ..\\..\\..\\..\\windows\\win.ini. ZIP and
API variants." Section 3's file comment repeats the same four-way split
("Windows + ZIP + API"). Only the first two have a concrete pattern
anywhere in the blueprint -- ZIP-based traversal (e.g. zip-slip-style
archive extraction abuse) and API-specific traversal patterns are named
as categories with no example payload, no target file, no detection rule
given in any of the 16 sections (grep-confirmed before this file was
written). Inventing a concrete ZIP/API payload and detection rule from
nothing would be exactly the silent invention the Engineering
Constitution's STOP CONDITIONS forbid -- flagged in
`path_traversal_payloads.json`'s own `_status` note and
docs/DECISIONS.md item 79, not silently built around.

LINUX DETECTION IS SHARED WITH `lfi_scanner.py` (docs/DECISIONS.md item
78): `core.scanners.content_heuristics.looks_like_hostname_content`,
identical function, identical target file (`/etc/hostname`) -- this
scanner's Linux coverage and `lfi_scanner.py`'s Linux coverage agree by
construction, not by two separately-maintained checks.

WINDOWS DETECTION USES `looks_like_win_ini_content` -- SEE THAT
FUNCTION'S DOCSTRING FOR WHY IT IS A RELIABLE EXACT MATCH, NOT A
HEURISTIC: `win.ini`'s `[fonts]`/`[extensions]` headers are fixed across
every stock Windows install.

NO WINDOWS-FINGERPRINT GATING HERE, UNLIKE `lfi_scanner.py`: Section
7.10's fingerprint-then-defer logic is explicitly `lfi_scanner.py`'s own
behavior (Section 3: "On Windows fingerprint... return 0
ExploitCandidates" is `lfi_scanner.py`'s comment, not this file's).
Section 7.18 gives this scanner no equivalent instruction to skip Linux
testing on a Windows-fingerprinted target or vice versa -- it tests both
Linux and Windows payloads against every target regardless of
fingerprint, consistent with it being the catch-all Section 3 already
describes it as ("handles Windows LFI when deferred" -- i.e. it is the
one that does NOT skip).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.content_heuristics import looks_like_hostname_content, looks_like_win_ini_content
from core.scanners.param_injection import iter_query_param_injections
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "path_traversal_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

# target_file -> the content check that applies to it. AUTHORED mapping,
# not blueprint-cited -- see module docstring for why only these two
# target files exist at all this batch.
_CONTENT_CHECKS = {
    "hostname": looks_like_hostname_content,
    "win_ini": looks_like_win_ini_content,
}


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from
    `path_traversal_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


@register("path_traversal")
class PathTraversalScanner(BaseScanner):
    """Section 7.18. See module docstring for scope (Linux + Windows
    only) and the shared content-heuristic design."""

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
            See `BaseScanner.scan`.
        """
        if not self._payloads or not iter_query_param_injections(target_url, "_probe"):
            return []

        baseline = await self.session.request("GET", target_url)

        candidates: list[ExploitCandidate] = []
        for payload_entry in self._payloads:
            payload = payload_entry["payload"]
            content_check = _CONTENT_CHECKS[payload_entry["target_file"]]

            for point in iter_query_param_injections(target_url, payload):
                response = await self.session.request("GET", point.url)
                if content_check(response.text, baseline.text):
                    candidates.append(
                        ExploitCandidate(
                            vuln_type="path_traversal",
                            endpoint=target_url,
                            http_method="GET",
                            parameter=point.parameter,
                            detected_by="path_traversal",
                            payload_used=payload,
                            raw_response_snapshot=response.text[:RAW_RESPONSE_SNAPSHOT_CHARS],
                        )
                    )
        return candidates
