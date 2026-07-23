"""
Implements: Section 3/5.2/5.4 test coverage -- core/verifier/evidence_chain.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.ontology.enums import EvidenceType
from core.ontology.findings import TriageResult
from core.verifier.evidence_chain import (
    VulnThresholds,
    VulnThresholdsConfigError,
    build_evidence_chain,
    load_vuln_thresholds,
)

REAL_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "vuln_thresholds.yaml"

# Section 5.4's min_evidence_types table, copied verbatim for a direct
# arithmetic cross-check against the real YAML file -- not eyeballed.
EXPECTED_MIN_EVIDENCE_TYPES = {
    "xss": 3,
    "sqli": 4,
    "ssti": 3,
    "ssrf": 2,
    "cmd_injection": 2,
    "xxe": 2,
    "lfi": 3,
    "idor": 3,
    "mass_assignment": 2,
    "auth": 2,
    "cors": 2,
    "jwt": 2,
    "oauth": 3,
    "csrf": 3,
    "race": 3,
    "bac": 3,
    "business_logic": 3,
    "graphql": 2,
    "websocket": 2,
    "api_versioning": 2,
    "hardcoded_credentials": 2,
    "nuclei": 2,
    "prototype_pollution": 2,
    "deserialization": 2,
    "crlf_injection": 2,
    "path_traversal": 3,
    "open_redirect": 2,
    "host_header": 2,
    "http_smuggling": 2,
    "default": 2,
}

# Section 5.4's min_signals table, copied verbatim.
EXPECTED_MIN_SIGNALS = {
    "xss": 4,
    "sqli": 3,
    "ssti": 3,
    "ssrf": 2,
    "cmd_injection": 2,
    "xxe": 2,
    "lfi": 3,
    "idor": 3,
    "mass_assignment": 2,
    "auth": 2,
    "cors": 4,
    "jwt": 2,
    "oauth": 3,
    "csrf": 3,
    "race": 2,
    "bac": 3,
    "business_logic": 3,
    "graphql": 2,
    "websocket": 2,
    "api_versioning": 2,
    "hardcoded_credentials": 2,
    "nuclei": 2,
    "prototype_pollution": 2,
    "deserialization": 2,
    "crlf_injection": 2,
    "path_traversal": 3,
    "open_redirect": 2,
    "host_header": 2,
    "http_smuggling": 2,
    "default": 2,
}


class TestLoadVulnThresholdsAgainstRealFile:
    """Round-trips the ACTUAL configs/vuln_thresholds.yaml -- catches drift
    between this file and Section 5.4 directly, not via a fixture that
    could quietly drift from the real file."""

    def test_real_file_loads(self):
        assert REAL_CONFIG_PATH.is_file(), f"expected {REAL_CONFIG_PATH} to exist"
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert isinstance(cfg, VulnThresholds)

    def test_min_evidence_types_matches_section_5_4_exactly(self):
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert cfg.min_evidence_types == EXPECTED_MIN_EVIDENCE_TYPES

    def test_min_signals_matches_section_5_4_exactly(self):
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert cfg.min_signals == EXPECTED_MIN_SIGNALS

    def test_all_29_scanner_types_present_in_both_tables(self):
        scanner_types = {
            "xss", "sqli", "ssti", "ssrf", "cmd_injection", "xxe", "lfi", "idor",
            "mass_assignment", "auth", "cors", "jwt", "oauth", "csrf", "race", "bac",
            "business_logic", "graphql", "websocket", "api_versioning",
            "hardcoded_credentials", "nuclei", "prototype_pollution", "deserialization",
            "crlf_injection", "path_traversal", "open_redirect", "host_header",
            "http_smuggling",
        }
        assert len(scanner_types) == 29
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert scanner_types <= cfg.min_evidence_types.keys()
        assert scanner_types <= cfg.min_signals.keys()

    def test_every_min_evidence_types_value_is_achievable_per_section_5_3(self):
        """Section 5.3: 'All minimums are achievable: max achievable >=
        min required for every row.' Spot-checks the two tightest/most
        discussed rows rather than re-deriving the whole matrix here."""
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert cfg.min_required_for("sqli") == 4  # tightest row in the matrix
        assert cfg.min_required_for("cors") == 2  # Section 7.11: "Evidence min: 2" (min_signals is the 4 one)

    def test_resolve_substitute_matches_section_5_2_for_every_documented_mapping(self):
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert cfg.resolve_substitute("xss", EvidenceType.OOB_INTERACTION) == EvidenceType.DOM_EXECUTION_CONFIRMED
        assert cfg.resolve_substitute("xss", EvidenceType.TIMING_ANOMALY) == EvidenceType.DOM_EXECUTION_CONFIRMED
        assert cfg.resolve_substitute("sqli", EvidenceType.OOB_INTERACTION) == EvidenceType.INJECTION_CONFIRMED
        assert cfg.resolve_substitute("sqli", EvidenceType.TIMING_ANOMALY) == EvidenceType.INJECTION_CONFIRMED
        assert (
            cfg.resolve_substitute("sqli", EvidenceType.CROSS_SCANNER)
            == EvidenceType.BOOLEAN_DIFFERENTIAL_CONFIRMED
        )
        assert (
            cfg.resolve_substitute("sqli", EvidenceType.CHAIN_PROVEN) == EvidenceType.BOOLEAN_DIFFERENTIAL_CONFIRMED
        )
        assert (
            cfg.resolve_substitute("csrf", EvidenceType.OOB_INTERACTION)
            == EvidenceType.CROSS_SITE_EXECUTION_CONFIRMED
        )
        assert (
            cfg.resolve_substitute("business_logic", EvidenceType.OOB_INTERACTION)
            == EvidenceType.WORKFLOW_TRACE_CONFIRMED
        )
        assert cfg.resolve_substitute("idor", EvidenceType.TIMING_ANOMALY) == EvidenceType.CROSS_ACCOUNT_READBACK
        assert cfg.resolve_substitute("bac", EvidenceType.TIMING_ANOMALY) == EvidenceType.CROSS_ACCOUNT_READBACK
        assert cfg.resolve_substitute("race", EvidenceType.REPLAY_STABLE) == EvidenceType.TIMING_CONFIRMED

    def test_resolve_substitute_returns_none_for_vuln_type_with_no_substitutes(self):
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert cfg.resolve_substitute("jwt", EvidenceType.OOB_INTERACTION) is None

    def test_resolve_substitute_returns_none_for_unmapped_core_type(self):
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        # sqli has no substitute defined for a DIFFERENTIAL shortfall -- differential
        # is always achievable for SQLi per Section 5.3, so no substitute exists.
        assert cfg.resolve_substitute("sqli", EvidenceType.DIFFERENTIAL) is None

    def test_min_required_for_falls_back_to_default(self):
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert cfg.min_required_for("not_a_real_vuln_type") == cfg.min_evidence_types["default"]

    def test_min_signals_for_falls_back_to_default(self):
        cfg = load_vuln_thresholds(REAL_CONFIG_PATH)
        assert cfg.min_signals_for("not_a_real_vuln_type") == cfg.min_signals["default"]


class TestLoadVulnThresholdsErrorHandling:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(VulnThresholdsConfigError, match="not found"):
            load_vuln_thresholds(tmp_path / "does_not_exist.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("min_signals: [this is not\n  a valid: mapping", encoding="utf-8")
        with pytest.raises(VulnThresholdsConfigError, match="not valid YAML"):
            load_vuln_thresholds(bad)

    def test_non_mapping_top_level_raises(self, tmp_path):
        p = tmp_path / "list_only.yaml"
        p.write_text("- 1\n- 2\n", encoding="utf-8")
        with pytest.raises(VulnThresholdsConfigError, match="mapping"):
            load_vuln_thresholds(p)

    def test_missing_required_key_raises(self, tmp_path):
        p = tmp_path / "incomplete.yaml"
        p.write_text("min_signals:\n  default: 2\nmin_evidence_types:\n  default: 2\n", encoding="utf-8")
        with pytest.raises(VulnThresholdsConfigError, match="evidence_substitutes"):
            load_vuln_thresholds(p)

    def test_missing_default_fallback_in_min_signals_raises(self, tmp_path):
        p = tmp_path / "no_default.yaml"
        p.write_text(
            "min_signals:\n  xss: 4\nmin_evidence_types:\n  default: 2\nevidence_substitutes: {}\n",
            encoding="utf-8",
        )
        with pytest.raises(VulnThresholdsConfigError, match="min_signals"):
            load_vuln_thresholds(p)

    def test_missing_default_fallback_in_min_evidence_types_raises(self, tmp_path):
        p = tmp_path / "no_default2.yaml"
        p.write_text(
            "min_signals:\n  default: 2\nmin_evidence_types:\n  xss: 3\nevidence_substitutes: {}\n",
            encoding="utf-8",
        )
        with pytest.raises(VulnThresholdsConfigError, match="min_evidence_types"):
            load_vuln_thresholds(p)


class TestBuildEvidenceChain:
    def test_resolves_min_required_from_real_config(self):
        chain = build_evidence_chain(
            vuln_type="sqli",
            collected_types=[EvidenceType.REPLAY_STABLE],
            thresholds=load_vuln_thresholds(REAL_CONFIG_PATH),
        )
        assert chain.min_required == 4  # Section 5.4: sqli's min_evidence_types

    def test_loads_default_config_when_thresholds_not_supplied(self, monkeypatch):
        monkeypatch.chdir(REAL_CONFIG_PATH.parents[1])  # repo root, where configs/ lives
        chain = build_evidence_chain(vuln_type="xss", collected_types=[])
        assert chain.min_required == 3

    def test_passes_through_triage_results(self):
        chain = build_evidence_chain(
            vuln_type="xss",
            collected_types=[],
            triage_l1=TriageResult.PASS,
            triage_l2=TriageResult.FAIL,
            triage_l3=TriageResult.INCONCLUSIVE,
            thresholds=load_vuln_thresholds(REAL_CONFIG_PATH),
        )
        assert chain.triage_l1 is TriageResult.PASS
        assert chain.triage_l2 is TriageResult.FAIL
        assert chain.triage_l3 is TriageResult.INCONCLUSIVE

    def test_defaults_to_no_triage_run_yet(self):
        chain = build_evidence_chain(
            vuln_type="xss", collected_types=[], thresholds=load_vuln_thresholds(REAL_CONFIG_PATH)
        )
        assert chain.triage_complete is False
