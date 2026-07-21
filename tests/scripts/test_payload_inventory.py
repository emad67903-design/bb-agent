"""
Implements: Section 3 test coverage -- payload_inventory.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import json

import pytest

from core.ontology.enums import PayloadFileType
from scripts.payload_inventory import (
    PAYLOAD_MANIFEST,
    PayloadInventoryError,
    check_files_exist_and_typed,
    check_manifest_counts,
    check_payload_engine_isolation,
    run_inventory,
)


class TestManifest:
    def test_has_26_entries(self):
        assert len(PAYLOAD_MANIFEST) == 26

    def test_24_injectable_2_pattern_library(self):
        injectable = [e for e in PAYLOAD_MANIFEST if e.file_type is PayloadFileType.INJECTABLE_PAYLOAD]
        pattern = [e for e in PAYLOAD_MANIFEST if e.file_type is PayloadFileType.PATTERN_LIBRARY]
        assert len(injectable) == 24
        assert len(pattern) == 2

    def test_pattern_library_files_are_exactly_the_two_named_in_section_3_1(self):
        pattern_names = {e.filename for e in PAYLOAD_MANIFEST if e.file_type is PayloadFileType.PATTERN_LIBRARY}
        assert pattern_names == {"hardcoded_credentials_patterns.json", "business_logic_patterns.json"}

    def test_no_duplicate_filenames(self):
        names = [e.filename for e in PAYLOAD_MANIFEST]
        assert len(names) == len(set(names))

    def test_check_manifest_counts_passes(self):
        check_manifest_counts()  # should not raise


class TestCheckFilesExistAndTyped:
    def test_all_missing_when_dir_empty(self, tmp_path):
        missing, mismatches = check_files_exist_and_typed(tmp_path)
        assert len(missing) == 26
        assert mismatches == []

    def test_passes_when_all_stubs_correctly_typed(self, tmp_path):
        for entry in PAYLOAD_MANIFEST:
            (tmp_path / entry.filename).write_text(
                json.dumps({"_file_type": entry.file_type.value, "payloads": []}), encoding="utf-8"
            )
        missing, mismatches = check_files_exist_and_typed(tmp_path)
        assert missing == []
        assert mismatches == []

    def test_detects_type_mismatch(self, tmp_path):
        for entry in PAYLOAD_MANIFEST:
            wrong_type = (
                PayloadFileType.PATTERN_LIBRARY
                if entry.file_type is PayloadFileType.INJECTABLE_PAYLOAD
                else PayloadFileType.INJECTABLE_PAYLOAD
            )
            (tmp_path / entry.filename).write_text(json.dumps({"_file_type": wrong_type.value}), encoding="utf-8")
        missing, mismatches = check_files_exist_and_typed(tmp_path)
        assert missing == []
        assert len(mismatches) == 26

    def test_detects_unreadable_json(self, tmp_path):
        (tmp_path / PAYLOAD_MANIFEST[0].filename).write_text("{not valid json", encoding="utf-8")
        for entry in PAYLOAD_MANIFEST[1:]:
            (tmp_path / entry.filename).write_text(
                json.dumps({"_file_type": entry.file_type.value}), encoding="utf-8"
            )
        missing, mismatches = check_files_exist_and_typed(tmp_path)
        assert missing == []
        assert len(mismatches) == 1
        assert mismatches[0][0] == PAYLOAD_MANIFEST[0].filename

    def test_real_generated_stub_directory_passes(self):
        # Regression check against the actual data/payloads/ stubs
        # committed for Week 0, not just synthetic tmp_path fixtures.
        from scripts.payload_inventory import DEFAULT_PAYLOADS_DIR

        missing, mismatches = check_files_exist_and_typed(DEFAULT_PAYLOADS_DIR)
        assert missing == [], f"missing real stub files: {missing}"
        assert mismatches == [], f"real stub files with wrong _file_type: {mismatches}"


class TestCheckPayloadEngineIsolation:
    def test_skipped_when_payload_engine_does_not_exist(self, tmp_path):
        result = check_payload_engine_isolation(tmp_path / "does_not_exist.py")
        assert result == "skipped"

    def test_passed_when_no_pattern_library_reference(self, tmp_path):
        path = tmp_path / "payload_engine.py"
        path.write_text("def render(template, context):\n    return xss_payloads_json_loaded\n", encoding="utf-8")
        assert check_payload_engine_isolation(path) == "passed"

    def test_failed_when_pattern_library_file_referenced(self, tmp_path):
        path = tmp_path / "payload_engine.py"
        path.write_text('PATTERNS = load("hardcoded_credentials_patterns.json")\n', encoding="utf-8")
        result = check_payload_engine_isolation(path)
        assert result.startswith("failed:")
        assert "hardcoded_credentials_patterns.json" in result

    def test_failed_lists_both_when_both_referenced(self, tmp_path):
        path = tmp_path / "payload_engine.py"
        path.write_text(
            'A = load("hardcoded_credentials_patterns.json")\nB = load("business_logic_patterns.json")\n',
            encoding="utf-8",
        )
        result = check_payload_engine_isolation(path)
        assert "hardcoded_credentials_patterns.json" in result
        assert "business_logic_patterns.json" in result


class TestRunInventory:
    def test_ok_true_with_correct_stubs_and_no_payload_engine(self, tmp_path):
        payloads_dir = tmp_path / "payloads"
        payloads_dir.mkdir()
        for entry in PAYLOAD_MANIFEST:
            (payloads_dir / entry.filename).write_text(
                json.dumps({"_file_type": entry.file_type.value}), encoding="utf-8"
            )
        report = run_inventory(payloads_dir=payloads_dir, payload_engine_path=tmp_path / "nonexistent.py")
        assert report.ok is True
        assert report.payload_engine_check == "skipped"

    def test_ok_false_when_files_missing(self, tmp_path):
        payloads_dir = tmp_path / "payloads"
        payloads_dir.mkdir()
        report = run_inventory(payloads_dir=payloads_dir, payload_engine_path=tmp_path / "nonexistent.py")
        assert report.ok is False
        assert len(report.missing_files) == 26

    def test_real_stub_directory_passes_full_inventory(self):
        from scripts.payload_inventory import DEFAULT_PAYLOADS_DIR

        report = run_inventory(payloads_dir=DEFAULT_PAYLOADS_DIR, payload_engine_path=DEFAULT_PAYLOADS_DIR / "nonexistent.py")
        assert report.ok is True
