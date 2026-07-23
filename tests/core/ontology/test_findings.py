"""
Implements: Section 3/6.8/6.9 test coverage -- core/ontology/findings.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import pytest

from core.ontology.enums import EvidenceType
from core.ontology.findings import (
    Evidence,
    EvidenceChain,
    Finding,
    Reviewability,
    TriageResult,
    compute_triage_score,
)


class TestTriageResult:
    def test_has_exactly_three_members(self):
        assert len(TriageResult) == 3

    def test_members(self):
        assert TriageResult.PASS == "pass"
        assert TriageResult.INCONCLUSIVE == "inconclusive"
        assert TriageResult.FAIL == "fail"


class TestComputeTriageScore:
    """Section 6.8's complete 9-state matrix, verbatim."""

    @pytest.mark.parametrize(
        "l1,l2,expected",
        [
            (TriageResult.PASS, TriageResult.PASS, 3),
            (TriageResult.PASS, TriageResult.INCONCLUSIVE, 1),
            (TriageResult.PASS, TriageResult.FAIL, 1),
            (TriageResult.INCONCLUSIVE, TriageResult.PASS, 1),
            (TriageResult.INCONCLUSIVE, TriageResult.INCONCLUSIVE, 0),
            (TriageResult.INCONCLUSIVE, TriageResult.FAIL, 0),
            (TriageResult.FAIL, TriageResult.PASS, 1),
            (TriageResult.FAIL, TriageResult.INCONCLUSIVE, 0),
            (TriageResult.FAIL, TriageResult.FAIL, 0),
        ],
    )
    def test_all_nine_states(self, l1, l2, expected):
        assert compute_triage_score(l1, l2) == expected

    def test_l3_not_in_signature(self):
        """Section 6.8: 'L3 is always INCONCLUSIVE for scoring -- it
        doesn't affect score.' Enforced structurally: the function only
        accepts two arguments."""
        import inspect

        params = inspect.signature(compute_triage_score).parameters
        assert list(params) == ["l1", "l2"]


class TestReviewability:
    def test_defaults_are_fail_closed(self):
        r = Reviewability()
        assert r.passes_signal_gate is False
        assert r.passes_blue_agent_gate is False
        assert r.signal_count == 0
        assert r.blue_agent_verdict == "REJECTED"

    def test_constructs_with_explicit_values(self):
        r = Reviewability(
            passes_signal_gate=True,
            passes_blue_agent_gate=True,
            signal_count=4,
            blue_agent_verdict="CONFIRMED",
        )
        assert r.passes_signal_gate is True
        assert r.blue_agent_verdict == "CONFIRMED"


def _chain(
    vuln_type: str = "xss",
    collected: list[EvidenceType] | None = None,
    min_required: int = 3,
    l1: TriageResult | None = TriageResult.PASS,
    l2: TriageResult | None = TriageResult.PASS,
    l3: TriageResult | None = TriageResult.PASS,
) -> EvidenceChain:
    return EvidenceChain(
        vuln_type=vuln_type,
        collected_types=collected if collected is not None else [],
        min_required=min_required,
        triage_l1=l1,
        triage_l2=l2,
        triage_l3=l3,
    )


class TestEvidenceChainCountingRule:
    """Section 5.2 COUNTING RULE: unique collected type = 1 slot."""

    def test_default_min_required_matches_yaml_default(self):
        assert EvidenceChain(vuln_type="xss").min_required == 2

    def test_duplicate_collected_types_count_once(self):
        chain = _chain(
            collected=[EvidenceType.REPLAY_STABLE, EvidenceType.REPLAY_STABLE, EvidenceType.DIFFERENTIAL],
            min_required=2,
        )
        assert chain.unique_collected_types == {EvidenceType.REPLAY_STABLE, EvidenceType.DIFFERENTIAL}
        assert chain.meets_per_vuln_minimum is True

    def test_substitute_collapses_to_one_slot_regardless_of_core_types_it_replaces(self):
        """SQLi: boolean_differential_confirmed substitutes for BOTH
        cross_scanner and chain_proven (Section 5.2) -- collecting it
        once must still be exactly 1 slot, not 2."""
        chain = _chain(
            vuln_type="sqli",
            collected=[
                EvidenceType.REPLAY_STABLE,
                EvidenceType.DIFFERENTIAL,
                EvidenceType.VARIANT_CONFIRMED,
                EvidenceType.BOOLEAN_DIFFERENTIAL_CONFIRMED,
            ],
            min_required=4,
        )
        assert len(chain.unique_collected_types) == 4
        assert chain.meets_per_vuln_minimum is True

    def test_below_minimum_fails(self):
        chain = _chain(collected=[EvidenceType.REPLAY_STABLE], min_required=3)
        assert chain.meets_per_vuln_minimum is False

    def test_exactly_at_minimum_passes(self):
        chain = _chain(
            collected=[EvidenceType.REPLAY_STABLE, EvidenceType.DIFFERENTIAL, EvidenceType.VARIANT_CONFIRMED],
            min_required=3,
        )
        assert chain.meets_per_vuln_minimum is True


class TestEvidenceChainTriageCompleteness:
    def test_incomplete_when_any_level_unset(self):
        assert _chain(l1=None).triage_complete is False
        assert _chain(l2=None).triage_complete is False
        assert _chain(l3=None).triage_complete is False

    def test_complete_when_all_three_set(self):
        assert _chain(l1=TriageResult.FAIL, l2=TriageResult.FAIL, l3=TriageResult.FAIL).triage_complete is True

    def test_triage_score_does_not_raise_when_incomplete(self):
        """Unset levels score as FAIL -- a defined default, not a crash --
        but this must NOT be confused with triage_complete (tested
        separately above); the two are independent signals."""
        chain = _chain(l1=None, l2=None)
        assert chain.triage_score == 0
        assert chain.triage_complete is False

    def test_l3_never_affects_score(self):
        base = _chain(l1=TriageResult.PASS, l2=TriageResult.PASS, l3=TriageResult.FAIL)
        other = _chain(l1=TriageResult.PASS, l2=TriageResult.PASS, l3=TriageResult.PASS)
        assert base.triage_score == other.triage_score == 3


class TestEvidenceChainStrength:
    """Section 6.8's formula and its documented [0.0, 1.0] bound (v6.4-008)."""

    def test_zero_evidence_zero_triage_is_zero(self):
        chain = _chain(collected=[], min_required=2, l1=TriageResult.FAIL, l2=TriageResult.FAIL)
        assert chain.evidence_ratio == 0.0
        assert chain.triage_ratio == 0.0
        assert chain.strength == 0.0

    def test_maximum_possible_strength_is_exactly_one(self):
        """v6.4-008 regression: 7 core types + 1 independently-firing
        substitute (realistic for SQLi per Section 5.3) must not push
        strength above 1.0."""
        all_core_plus_one_substitute = [
            EvidenceType.REPLAY_STABLE,
            EvidenceType.OOB_INTERACTION,
            EvidenceType.DIFFERENTIAL,
            EvidenceType.TIMING_ANOMALY,
            EvidenceType.CROSS_SCANNER,
            EvidenceType.VARIANT_CONFIRMED,
            EvidenceType.CHAIN_PROVEN,
            EvidenceType.BOOLEAN_DIFFERENTIAL_CONFIRMED,  # 8th unique type
        ]
        chain = _chain(
            vuln_type="sqli",
            collected=all_core_plus_one_substitute,
            min_required=4,
            l1=TriageResult.PASS,
            l2=TriageResult.PASS,
        )
        assert len(chain.unique_collected_types) == 8  # confirms the cap is actually exercised
        assert chain.evidence_ratio == 1.0
        assert chain.triage_ratio == 1.0
        assert chain.strength == 1.0

    def test_worked_example_from_section_6_8(self):
        """3 unique types, PASS+INCONCLUSIVE triage (score=1):
        evidence_ratio = 3/7, triage_ratio = 1/3
        strength = round(0.7*(3/7) + 0.3*(1/3), 3) = round(0.3 + 0.1, 3) = 0.4
        """
        chain = _chain(
            collected=[EvidenceType.REPLAY_STABLE, EvidenceType.DIFFERENTIAL, EvidenceType.VARIANT_CONFIRMED],
            min_required=3,
            l1=TriageResult.PASS,
            l2=TriageResult.INCONCLUSIVE,
        )
        assert chain.triage_score == 1
        assert chain.strength == 0.4

    @pytest.mark.parametrize(
        "strength,expected_label",
        [
            (0.71, "auto_report"),
            (1.0, "auto_report"),
            (0.70, "report_with_notes"),
            (0.43, "report_with_notes"),
            (0.42, "deep_verify"),
            (0.29, "deep_verify"),
            (0.28, "low_confidence"),
            (0.0, "low_confidence"),
        ],
    )
    def test_strength_label_boundaries(self, strength, expected_label, monkeypatch):
        chain = _chain()
        monkeypatch.setattr(type(chain), "strength", property(lambda self: strength))
        assert chain.strength_label == expected_label


class TestEvidence:
    def test_defaults_are_fail_closed(self):
        ev = Evidence(evidence_chain=_chain())
        assert ev.exploit_executed is False
        assert ev.exploit_safe is False
        assert ev.verified is False

    def test_requires_evidence_chain(self):
        with pytest.raises(TypeError):
            Evidence()  # type: ignore[call-arg]


class TestFindingIsReportable:
    """Section 6.9's PoC Gate -- exercises every conjunct independently
    so a future edit that accidentally drops one is caught."""

    def _finding(self, **overrides) -> Finding:
        chain = _chain(min_required=1, collected=[EvidenceType.REPLAY_STABLE])
        evidence = Evidence(
            evidence_chain=chain,
            exploit_executed=overrides.get("exploit_executed", True),
            exploit_safe=overrides.get("exploit_safe", True),
            verified=overrides.get("verified", True),
        )
        reviewability = Reviewability(
            passes_signal_gate=overrides.get("passes_signal_gate", True),
            passes_blue_agent_gate=overrides.get("passes_blue_agent_gate", True),
        )
        return Finding(
            finding_id="F-1",
            vuln_type="xss",
            endpoint="/test",
            evidence=evidence,
            reviewability=reviewability,
        )

    def test_all_true_is_reportable(self):
        assert self._finding().is_reportable is True

    def test_default_reviewability_is_fail_closed(self):
        """A Finding constructed with the default Reviewability() (no
        Fast Lane / blue_agent.py having run yet) must never be
        reportable."""
        chain = _chain(min_required=1, collected=[EvidenceType.REPLAY_STABLE])
        evidence = Evidence(evidence_chain=chain, exploit_executed=True, exploit_safe=True, verified=True)
        finding = Finding(finding_id="F-2", vuln_type="xss", endpoint="/test", evidence=evidence)
        assert finding.reviewability == Reviewability()
        assert finding.is_reportable is False

    @pytest.mark.parametrize(
        "flag",
        ["exploit_executed", "exploit_safe", "verified", "passes_signal_gate", "passes_blue_agent_gate"],
    )
    def test_any_single_false_conjunct_blocks_reportability(self, flag):
        assert self._finding(**{flag: False}).is_reportable is False

    def test_evidence_chain_below_minimum_blocks_reportability(self):
        finding = self._finding()
        finding.evidence.evidence_chain.min_required = 99
        assert finding.evidence.evidence_chain.meets_per_vuln_minimum is False
        assert finding.is_reportable is False

    def test_exploit_executed_and_dom_execution_confirmed_are_independent(self):
        """Section 7.1, V6.4-M4: exploit_executed=True does not imply any
        particular EvidenceType fired, and vice versa -- verified here at
        the Evidence level since Evidence is what carries both."""
        chain = _chain(min_required=1, collected=[])  # no EvidenceType collected at all
        evidence = Evidence(evidence_chain=chain, exploit_executed=True, exploit_safe=True, verified=True)
        assert evidence.exploit_executed is True
        assert EvidenceType.DOM_EXECUTION_CONFIRMED not in evidence.evidence_chain.unique_collected_types
