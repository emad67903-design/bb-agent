"""
Implements: Section 3/6.8 test coverage -- core/verifier/deterministic_verifier.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import logging

import pytest

from core.ontology.enums import EvidenceType
from core.ontology.findings import Evidence, EvidenceChain, Finding, Reviewability, TriageResult
from core.verifier.deterministic_verifier import verify_finding


def _finding(
    triage_l1: TriageResult | None,
    triage_l2: TriageResult | None,
    triage_l3: TriageResult | None,
    collected: list[EvidenceType] | None = None,
    min_required: int = 99,  # deliberately high so strength stays low unless overridden
) -> Finding:
    chain = EvidenceChain(
        vuln_type="xss",
        collected_types=collected or [],
        min_required=min_required,
        triage_l1=triage_l1,
        triage_l2=triage_l2,
        triage_l3=triage_l3,
    )
    evidence = Evidence(evidence_chain=chain)
    return Finding(finding_id="F-TEST", vuln_type="xss", endpoint="/test", evidence=evidence)


class TestVerifiedFlag:
    def test_true_when_triage_complete_and_harness_invoked(self):
        finding = _finding(TriageResult.PASS, TriageResult.PASS, TriageResult.PASS)
        result = verify_finding(finding, harness_invoked=True)
        assert result.evidence.verified is True

    def test_false_when_harness_not_invoked(self):
        finding = _finding(TriageResult.PASS, TriageResult.PASS, TriageResult.PASS)
        result = verify_finding(finding, harness_invoked=False)
        assert result.evidence.verified is False

    @pytest.mark.parametrize(
        "l1,l2,l3",
        [
            (None, TriageResult.PASS, TriageResult.PASS),
            (TriageResult.PASS, None, TriageResult.PASS),
            (TriageResult.PASS, TriageResult.PASS, None),
            (None, None, None),
        ],
    )
    def test_false_when_triage_incomplete_even_if_harness_invoked(self, l1, l2, l3):
        finding = _finding(l1, l2, l3)
        result = verify_finding(finding, harness_invoked=True)
        assert result.evidence.verified is False

    def test_means_workflow_ran_not_vuln_confirmed(self):
        """Section 6.8: verified=True even when triage FAILED outright,
        as long as all 3 levels completed and the harness ran -- this
        flag tracks workflow completeness, not a positive finding."""
        finding = _finding(TriageResult.FAIL, TriageResult.FAIL, TriageResult.FAIL)
        result = verify_finding(finding, harness_invoked=True)
        assert result.evidence.verified is True

    def test_returns_same_instance(self):
        finding = _finding(TriageResult.PASS, TriageResult.PASS, TriageResult.PASS)
        result = verify_finding(finding, harness_invoked=True)
        assert result is finding

    def test_does_not_touch_reviewability(self):
        finding = _finding(TriageResult.PASS, TriageResult.PASS, TriageResult.PASS)
        finding.reviewability = Reviewability(passes_signal_gate=True, blue_agent_verdict="CONFIRMED")
        before = finding.reviewability
        verify_finding(finding, harness_invoked=True)
        assert finding.reviewability is before


class TestLowConfidenceLogging:
    def test_logs_low_confidence_tag_when_strength_is_low(self, caplog):
        finding = _finding(
            TriageResult.FAIL,
            TriageResult.FAIL,
            TriageResult.FAIL,
            collected=[],
            min_required=99,
        )
        assert finding.evidence.evidence_chain.strength_label == "low_confidence"
        with caplog.at_level(logging.INFO, logger="core.verifier.deterministic_verifier"):
            verify_finding(finding, harness_invoked=True)
        assert any("[LOW_CONFIDENCE]" in r.message for r in caplog.records)
        assert any("F-TEST" in r.message for r in caplog.records)
        assert any("xss" in r.message for r in caplog.records)

    def test_no_low_confidence_log_when_strength_is_high(self, caplog):
        finding = _finding(
            TriageResult.PASS,
            TriageResult.PASS,
            TriageResult.PASS,
            collected=[
                EvidenceType.REPLAY_STABLE,
                EvidenceType.DIFFERENTIAL,
                EvidenceType.VARIANT_CONFIRMED,
            ],
            min_required=3,
        )
        assert finding.evidence.evidence_chain.strength_label != "low_confidence"
        with caplog.at_level(logging.INFO, logger="core.verifier.deterministic_verifier"):
            verify_finding(finding, harness_invoked=True)
        assert not any("[LOW_CONFIDENCE]" in r.message for r in caplog.records)
