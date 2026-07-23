"""
Implements: Section 3 test coverage -- core/ontology/enums.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import pytest

from core.ontology.enums import EvidenceType, PayloadFileType


class TestPayloadFileType:
    def test_has_exactly_two_members(self):
        assert len(PayloadFileType) == 2

    def test_values_match_section_3_1(self):
        assert PayloadFileType.INJECTABLE_PAYLOAD.value == "injectable_payload"
        assert PayloadFileType.PATTERN_LIBRARY.value == "pattern_library"

    def test_string_mixin_serializes_as_plain_value(self):
        assert PayloadFileType.INJECTABLE_PAYLOAD == "injectable_payload"
        assert f"{PayloadFileType.PATTERN_LIBRARY.value}" == "pattern_library"

    def test_constructs_from_string_value(self):
        assert PayloadFileType("injectable_payload") is PayloadFileType.INJECTABLE_PAYLOAD
        assert PayloadFileType("pattern_library") is PayloadFileType.PATTERN_LIBRARY

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            PayloadFileType("not_a_real_type")


class TestEvidenceType:
    """Section 5.1 (7 core) + Section 5.2 (7 substitutes) = 14 total."""

    def test_has_exactly_fourteen_members(self):
        assert len(EvidenceType) == 14

    def test_seven_core_types_match_section_5_1(self):
        core = {
            EvidenceType.REPLAY_STABLE: "replay_stable",
            EvidenceType.OOB_INTERACTION: "oob_interaction",
            EvidenceType.DIFFERENTIAL: "differential",
            EvidenceType.TIMING_ANOMALY: "timing_anomaly",
            EvidenceType.CROSS_SCANNER: "cross_scanner",
            EvidenceType.VARIANT_CONFIRMED: "variant_confirmed",
            EvidenceType.CHAIN_PROVEN: "chain_proven",
        }
        assert len(core) == 7
        for member, value in core.items():
            assert member.value == value

    def test_seven_substitute_types_match_section_5_2(self):
        substitutes = {
            EvidenceType.DOM_EXECUTION_CONFIRMED: "dom_execution_confirmed",
            EvidenceType.INJECTION_CONFIRMED: "injection_confirmed",
            EvidenceType.BOOLEAN_DIFFERENTIAL_CONFIRMED: "boolean_differential_confirmed",
            EvidenceType.CROSS_SITE_EXECUTION_CONFIRMED: "cross_site_execution_confirmed",
            EvidenceType.WORKFLOW_TRACE_CONFIRMED: "workflow_trace_confirmed",
            EvidenceType.CROSS_ACCOUNT_READBACK: "cross_account_readback",
            EvidenceType.TIMING_CONFIRMED: "timing_confirmed",
        }
        assert len(substitutes) == 7
        for member, value in substitutes.items():
            assert member.value == value

    def test_string_mixin_serializes_as_plain_value(self):
        assert EvidenceType.REPLAY_STABLE == "replay_stable"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            EvidenceType("not_a_real_evidence_type")
