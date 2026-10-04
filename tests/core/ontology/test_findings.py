"""
Implements: Section 3/6.8/6.9 test coverage -- core/ontology/findings.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.ontology.enums import EvidenceType
from core.ontology.findings import (
    Evidence,
    EvidenceChain,
    ExploitCandidate,
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


class TestExploitCandidate:
    """docs/DECISIONS.md item 69."""

    def _candidate(self, **overrides):
        defaults = dict(
            vuln_type="xss",
            endpoint="/search",
            http_method="GET",
            parameter="q",
            detected_by="xss_scanner",
        )
        defaults.update(overrides)
        return ExploitCandidate(**defaults)

    def test_required_fields_construct_with_no_defaults_needed(self):
        c = self._candidate()
        assert (c.vuln_type, c.endpoint, c.http_method, c.parameter, c.detected_by) == (
            "xss",
            "/search",
            "GET",
            "q",
            "xss_scanner",
        )

    @pytest.mark.parametrize("vuln_type", ["cors", "host_header", "csrf", "auth"])
    def test_parameter_accepts_none_for_the_four_no_single_parameter_vuln_types(self, vuln_type):
        """Section 6.9's DEDUP_KEY; the four cases confirmed in
        docs/DECISIONS.md item 69: CORS (Origin header), Host Header
        (Host header), CSRF (whole-form action), Auth (state-machine
        test name) have no single query/body parameter."""
        c = self._candidate(vuln_type=vuln_type, parameter=None)
        assert c.parameter is None

    def test_parameter_is_required_not_defaulted(self):
        """Unlike payload_used/raw_response_snapshot/probe_correlation_id
        below, `parameter` has no default -- callers must explicitly
        pass a value or explicit None, never omit it (class docstring)."""
        with pytest.raises(TypeError):
            ExploitCandidate(vuln_type="xss", endpoint="/search", http_method="GET", detected_by="xss_scanner")

    def test_detected_at_defaults_to_now_utc(self):
        before = datetime.now(timezone.utc)
        c = self._candidate()
        after = datetime.now(timezone.utc)
        assert before <= c.detected_at <= after
        assert c.detected_at.tzinfo is not None  # timezone-aware, not naive

    def test_detected_at_is_overridable_for_deterministic_tests(self):
        fixed = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
        c = self._candidate(detected_at=fixed)
        assert c.detected_at == fixed

    def test_batch_1_optional_fields_default_to_none(self):
        """docs/DECISIONS.md item 69: payload_used/raw_response_snapshot/
        probe_correlation_id are Batch 1's hypothesis, not required by
        every vuln_type (e.g. probe_correlation_id is None for all of
        Batch 1, which has no OOB-based detection)."""
        c = self._candidate()
        assert (c.payload_used, c.raw_response_snapshot, c.probe_correlation_id) == (None, None, None)

    def test_batch_1_optional_fields_can_be_set(self):
        c = self._candidate(
            vuln_type="sqli",
            payload_used="' OR 1=1--",
            raw_response_snapshot="HTTP/1.1 200 OK...",
            probe_correlation_id=None,
        )
        assert c.payload_used == "' OR 1=1--"
        assert c.raw_response_snapshot == "HTTP/1.1 200 OK..."

    def test_probe_correlation_id_set_for_oob_style_detection(self):
        """Batch 2 preview (CMDi/XXE/Deserialization/SSRF/Host Header) --
        not this bundle's scope to build the scanners, but the field
        must actually hold an interactsh-style correlation ID (Section
        4.2) when a caller supplies one."""
        c = self._candidate(vuln_type="cmd_injection", parameter="cmd", probe_correlation_id="XBOW_sess123_ab12")
        assert c.probe_correlation_id == "XBOW_sess123_ab12"

    def test_race_fields_default_to_none(self):
        """docs/DECISIONS.md item 105 (race_scanner.py pre-investigation,
        not yet built): success_count/total_requests exist for the
        `race` vuln_type only; every other candidate leaves them unset,
        same "None means not applicable" convention
        test_batch_1_optional_fields_default_to_none already pins for
        payload_used/raw_response_snapshot/probe_correlation_id."""
        c = self._candidate()
        assert (c.success_count, c.total_requests) == (None, None)

    def test_race_fields_can_be_set_independently_of_other_optional_fields(self):
        """Confirms success_count/total_requests are genuinely separate
        fields, added at the end of the dataclass, not a tuple/combined
        value, and do not disturb payload_used/raw_response_snapshot/
        probe_correlation_id's own independent defaults."""
        c = self._candidate(
            vuln_type="race",
            parameter=None,
            success_count=3,
            total_requests=30,
        )
        assert c.success_count == 3
        assert c.total_requests == 30
        assert (c.payload_used, c.raw_response_snapshot, c.probe_correlation_id) == (None, None, None)

    def test_equality_is_by_value_when_detected_at_matches(self):
        """Dataclass default __eq__ -- pinning this since detected_at's
        default_factory would otherwise make two "identical" candidates
        compare unequal by construction-time timestamp alone."""
        fixed = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
        c1 = self._candidate(detected_at=fixed)
        c2 = self._candidate(detected_at=fixed)
        assert c1 == c2
        assert c1 is not c2

    def test_two_candidates_constructed_separately_have_distinct_detected_at_by_default(self):
        """The inverse of the above -- confirms detected_at's
        default_factory actually re-evaluates per instance rather than
        sharing one frozen default (the classic mutable-default-argument
        class of bug, guarded against here the way this file already
        expects EvidenceChain's own field(default_factory=list) to
        behave)."""
        c1 = self._candidate()
        import time

        time.sleep(0.001)
        c2 = self._candidate()
        assert c1.detected_at != c2.detected_at

    def test_survives_a_dict_round_trip_via_isoformat(self):
        """Lightweight round-trip check (Engineering Constitution:
        "Every ontology dataclass/enum change gets a serialization
        round-trip test... this project already had a graph-
        serialization bug in a prior version"). No dedicated
        serialize_exploit_candidate()/deserialize_exploit_candidate()
        exists yet (out of this bundle's scope -- nothing persists an
        ExploitCandidate to PostgreSQL/JSON this week) -- this test
        instead pins that `dataclasses.asdict()` plus the same
        isoformat()/fromisoformat() convention Section 11.3 already
        established for BeliefGraph's own datetime fields round-trips
        cleanly, so a future real serializer has a confirmed-safe
        pattern to build on rather than discovering datetime handling
        issues then."""
        import dataclasses

        fixed = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
        c = self._candidate(detected_at=fixed, payload_used="<script>alert(1)</script>")
        as_dict = dataclasses.asdict(c)
        as_dict["detected_at"] = as_dict["detected_at"].isoformat()

        restored_dict = dict(as_dict)
        restored_dict["detected_at"] = datetime.fromisoformat(restored_dict["detected_at"])
        restored = ExploitCandidate(**restored_dict)

        assert restored == c

    def test_race_fields_survive_a_dict_round_trip(self):
        """Same round-trip convention as test_survives_a_dict_round_
        trip_via_isoformat above, with success_count/total_requests set
        to real, distinguishable (non-None, unequal-to-each-other)
        values -- docs/DECISIONS.md item 105's own required round-trip
        test for this ontology addition (Engineering Constitution), kept
        as its own test rather than folded into the existing one so a
        future failure specifically in these two new fields is
        unambiguous about which addition broke, not just "the round-
        trip test failed"."""
        import dataclasses

        fixed = datetime(2026, 9, 20, 9, 0, 0, tzinfo=timezone.utc)
        c = self._candidate(
            vuln_type="race",
            parameter=None,
            detected_at=fixed,
            success_count=3,
            total_requests=30,
        )
        as_dict = dataclasses.asdict(c)
        as_dict["detected_at"] = as_dict["detected_at"].isoformat()

        restored_dict = dict(as_dict)
        restored_dict["detected_at"] = datetime.fromisoformat(restored_dict["detected_at"])
        restored = ExploitCandidate(**restored_dict)

        assert restored == c
        assert restored.success_count == 3
        assert restored.total_requests == 30
