#!/usr/bin/env python3
"""
Implements: Section 3 -- scripts/payload_inventory.py ("Validates 26
files; enforces type classification; FAILS if pattern_library file
referenced in payload_engine") and Section 3.1 (Payload File Inventory).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 0 SCOPE NOTE: The 26 files are created this week as schema-correct
STUBS (agreed resolution, item 2) -- correct filename, correct
PayloadFileType classification, placeholder content. Real attack-payload
content is added per-scanner starting Week 7, alongside the scanner that
consumes each file (Section 12). This script validates STRUCTURE
(existence + type classification), not payload content quality, in
either phase.

The "FAILS if pattern_library file referenced in payload_engine" check
requires core/knowledge/payload_engine.py, which does not exist until a
later week. That sub-check is SKIPPED (not silently passed) until the
file exists -- see check_payload_engine_isolation().
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from core.ontology.enums import PayloadFileType

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAYLOADS_DIR = REPO_ROOT / "data" / "payloads"
DEFAULT_PAYLOAD_ENGINE_PATH = REPO_ROOT / "core" / "knowledge" / "payload_engine.py"


@dataclass(frozen=True)
class PayloadManifestEntry:
    """One row of Section 3.1's 26-file inventory table.

    Attributes:
        filename: Exact filename under data/payloads/.
        file_type: injectable_payload or pattern_library.
        scanner_id: The scanner that owns this file.
        notes: Section 3.1's notes column, kept for traceability.
    """

    filename: str
    file_type: PayloadFileType
    scanner_id: str
    notes: str


# Section 3.1's 26-row table, verbatim. This is the single source of
# truth for "which payload files should exist" -- do not duplicate this
# list anywhere else (mirrors the SCANNER_REGISTRY single-source rule).
PAYLOAD_MANIFEST: tuple[PayloadManifestEntry, ...] = (
    PayloadManifestEntry("xss_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "xss_scanner", "HTML/attr/JS context/DOM probes"),
    PayloadManifestEntry("sqli_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "sqli_scanner", "Error/boolean/time/union probes"),
    PayloadManifestEntry("ssti_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "ssti_scanner", "Jinja2/Twig/Freemarker/etc."),
    PayloadManifestEntry("ssrf_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "ssrf_scanner", "Internal metadata URLs + interactsh OOB"),
    PayloadManifestEntry("lfi_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "lfi_scanner", "Linux paths only (/etc/hostname etc.)"),
    PayloadManifestEntry("cmd_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "cmd_injection", "OOB interactsh commands only"),
    PayloadManifestEntry("prototype_pollution_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "prototype_pollution", "__proto__/constructor.prototype probes"),
    PayloadManifestEntry("deserialization_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "deserialization", "Java/Python/PHP OOB gadgets"),
    PayloadManifestEntry("crlf_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "crlf_injection", r"\r\n header injection sequences"),
    PayloadManifestEntry("path_traversal_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "path_traversal", "Windows + ZIP + API + Linux sequences"),
    PayloadManifestEntry("redirect_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "open_redirect", "Protocol-relative, javascript:, data:"),
    PayloadManifestEntry("smuggling_configs.json", PayloadFileType.INJECTABLE_PAYLOAD, "http_smuggling", "CL vs TE variants"),
    PayloadManifestEntry("xxe_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "xxe_scanner", "OOB DTD, file read, SSRF via XXE"),
    PayloadManifestEntry("jwt_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "jwt_scanner", "alg:none, RS256->HS256, weak secrets"),
    PayloadManifestEntry("oauth_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "oauth_scanner", "redirect_uri, state, leakage"),
    PayloadManifestEntry("csrf_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "csrf_scanner", "Cross-site form tests"),
    PayloadManifestEntry("websocket_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "websocket_scanner", "auth bypass, injection, WS-CSRF"),
    PayloadManifestEntry("graphql_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "graphql_scanner", "introspection, IDOR, batching"),
    PayloadManifestEntry("api_versioning_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "api_versioning", "/v1/ vs /v2/ access tests"),
    PayloadManifestEntry("hardcoded_credentials_patterns.json", PayloadFileType.PATTERN_LIBRARY, "hardcoded_credentials", "Regex patterns -- NOT injectable"),
    PayloadManifestEntry("race_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "race_scanner", "Concurrent request templates"),
    PayloadManifestEntry("bac_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "bac_scanner", "Permission graph tests"),
    PayloadManifestEntry("business_logic_patterns.json", PayloadFileType.PATTERN_LIBRARY, "business_logic", "Workflow patterns -- NOT injectable"),
    PayloadManifestEntry("host_header_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "host_header", "Password reset poisoning"),
    PayloadManifestEntry("http_smuggling_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "http_smuggling", "Raw-byte variants"),
    PayloadManifestEntry("mass_assignment_payloads.json", PayloadFileType.INJECTABLE_PAYLOAD, "mass_assignment", "Extra field injection"),
)

EXPECTED_INJECTABLE_COUNT = 24  # Section 3.1 summary line
EXPECTED_PATTERN_LIBRARY_COUNT = 2  # Section 3.1 summary line


class PayloadInventoryError(Exception):
    """Raised on any inventory validation failure."""


@dataclass
class PayloadInventoryReport:
    """Full result of a payload_inventory.py run.

    Attributes:
        missing_files: Manifest filenames not found on disk.
        type_mismatches: (filename, expected, actual) where a file's
            content-declared type disagrees with the manifest.
        payload_engine_check: "skipped" | "passed" | "failed:<filenames>".
    """

    missing_files: list[str]
    type_mismatches: list[tuple[str, str, str]]
    payload_engine_check: str

    @property
    def ok(self) -> bool:
        return (
            not self.missing_files
            and not self.type_mismatches
            and not self.payload_engine_check.startswith("failed")
        )


def check_manifest_counts() -> None:
    """Sanity-checks PAYLOAD_MANIFEST itself against Section 3.1's summary
    line ("24 injectable_payload + 2 pattern_library = 26 files")."""
    injectable = sum(1 for e in PAYLOAD_MANIFEST if e.file_type is PayloadFileType.INJECTABLE_PAYLOAD)
    pattern = sum(1 for e in PAYLOAD_MANIFEST if e.file_type is PayloadFileType.PATTERN_LIBRARY)
    if len(PAYLOAD_MANIFEST) != 26:
        raise PayloadInventoryError(f"PAYLOAD_MANIFEST has {len(PAYLOAD_MANIFEST)} entries, expected 26")
    if injectable != EXPECTED_INJECTABLE_COUNT:
        raise PayloadInventoryError(
            f"PAYLOAD_MANIFEST has {injectable} injectable_payload entries, expected {EXPECTED_INJECTABLE_COUNT}"
        )
    if pattern != EXPECTED_PATTERN_LIBRARY_COUNT:
        raise PayloadInventoryError(
            f"PAYLOAD_MANIFEST has {pattern} pattern_library entries, expected {EXPECTED_PATTERN_LIBRARY_COUNT}"
        )


def check_files_exist_and_typed(payloads_dir: Path) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Checks every manifest file exists and its embedded `_file_type`
    matches the manifest's classification.

    Each stub/real payload file carries its own `_file_type` field so
    this check works identically on Week 0 stubs and Week 7 real content
    -- the file's declared type must always agree with Section 3.1's
    manifest, at every stage of the file's life.

    Args:
        payloads_dir: Directory containing the 26 payload files.

    Returns:
        (missing_files, type_mismatches)
    """
    missing: list[str] = []
    mismatches: list[tuple[str, str, str]] = []

    for entry in PAYLOAD_MANIFEST:
        path = payloads_dir / entry.filename
        if not path.is_file():
            missing.append(entry.filename)
            continue
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            mismatches.append((entry.filename, entry.file_type.value, f"<unreadable: {exc}>"))
            continue
        actual = content.get("_file_type") if isinstance(content, dict) else None
        if actual != entry.file_type.value:
            mismatches.append((entry.filename, entry.file_type.value, str(actual)))

    return missing, mismatches


def check_payload_engine_isolation(payload_engine_path: Path) -> str:
    """Section 3 rule: "FAILS if pattern_library file referenced in
    payload_engine" -- Section 3's own payload_engine.py contract says it
    "Processes injectable_payload type ONLY", so a pattern_library
    filename appearing in that file is a contract violation.

    Args:
        payload_engine_path: Path to core/knowledge/payload_engine.py.

    Returns:
        "skipped" if the file does not exist yet (Week 0: it doesn't),
        "passed" if it exists and references no pattern_library filename,
        "failed:<filenames>" otherwise.
    """
    if not payload_engine_path.is_file():
        return "skipped"

    text = payload_engine_path.read_text(encoding="utf-8")
    pattern_library_files = [
        e.filename for e in PAYLOAD_MANIFEST if e.file_type is PayloadFileType.PATTERN_LIBRARY
    ]
    referenced = [f for f in pattern_library_files if re.search(re.escape(f), text)]
    if referenced:
        return f"failed:{','.join(referenced)}"
    return "passed"


def run_inventory(
    payloads_dir: Path = DEFAULT_PAYLOADS_DIR,
    payload_engine_path: Path = DEFAULT_PAYLOAD_ENGINE_PATH,
) -> PayloadInventoryReport:
    """Runs the full Section 3 payload_inventory.py check."""
    check_manifest_counts()
    missing, mismatches = check_files_exist_and_typed(payloads_dir)
    engine_check = check_payload_engine_isolation(payload_engine_path)
    return PayloadInventoryReport(
        missing_files=missing, type_mismatches=mismatches, payload_engine_check=engine_check
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads-dir", type=Path, default=DEFAULT_PAYLOADS_DIR)
    parser.add_argument("--payload-engine", type=Path, default=DEFAULT_PAYLOAD_ENGINE_PATH)
    args = parser.parse_args()

    try:
        report = run_inventory(args.payloads_dir, args.payload_engine)
    except PayloadInventoryError as exc:
        print(f"[PAYLOAD_INVENTORY_FAILED] {exc}", file=sys.stderr)
        return 1

    print(f"[PAYLOAD_INVENTORY] {len(PAYLOAD_MANIFEST)} manifest entries checked against {args.payloads_dir}")
    for f in report.missing_files:
        print(f"[PAYLOAD_INVENTORY_FAILED] missing file: {f}")
    for filename, expected, actual in report.type_mismatches:
        print(f"[PAYLOAD_INVENTORY_FAILED] {filename}: expected type={expected}, got={actual}")
    print(f"[PAYLOAD_INVENTORY] payload_engine isolation check: {report.payload_engine_check}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
