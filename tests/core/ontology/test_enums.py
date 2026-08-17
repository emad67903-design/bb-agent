"""
Implements: Section 3 test coverage -- core/ontology/enums.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import pytest

from core.ontology.enums import (
    BusinessValue,
    EvidenceType,
    InteractshMode,
    OOBPollOutcome,
    PayloadFileType,
    TargetType,
    TierLevel,
)


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


class TestTierLevel:
    """Section 10.1's four Permanent Tier Boundaries. Week 2 authorized
    addition (docs/DECISIONS.md) -- these tests exist specifically to
    pin the ordinal behavior Section 10.1's own code depends on
    (`if requested_tier > TierLevel.TIER_B`), not just the values.
    """

    def test_has_exactly_four_members(self):
        assert len(TierLevel) == 4

    def test_values_match_authorized_ordinal_assignment(self):
        assert TierLevel.TIER_A == 1
        assert TierLevel.TIER_B == 2
        assert TierLevel.TIER_C == 3
        assert TierLevel.TIER_D == 4

    def test_ordering_matches_section_10_1_risk_order(self):
        assert TierLevel.TIER_A < TierLevel.TIER_B < TierLevel.TIER_C < TierLevel.TIER_D

    def test_section_10_1_vdp_comparison_examples(self):
        """`if requested_tier > TierLevel.TIER_B` -- the exact expression
        Section 10.1's code block uses. TIER_C and TIER_D must both
        exceed TIER_B; TIER_A and TIER_B must not."""
        assert TierLevel.TIER_C > TierLevel.TIER_B
        assert TierLevel.TIER_D > TierLevel.TIER_B
        assert not (TierLevel.TIER_B > TierLevel.TIER_B)
        assert not (TierLevel.TIER_A > TierLevel.TIER_B)

    def test_is_intenum_not_str_mixin(self):
        """Deliberately NOT a (str, Enum) mixin like PayloadFileType/
        EvidenceType -- see module docstring: a str-mixin's descriptive
        slugs (read_only/low_risk_probe/state_changing/destructive) do
        not sort in risk order, which would make `>` silently wrong."""
        assert isinstance(TierLevel.TIER_C, int)
        assert not isinstance(TierLevel.TIER_C, str)

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            TierLevel(99)


class TestTargetType:
    """Section 3, 7 members, verbatim -- Week 3 addition."""

    def test_has_exactly_seven_members(self):
        assert len(TargetType) == 7

    def test_values_match_section_3_verbatim(self):
        assert TargetType.SPA.value == "spa"
        assert TargetType.REST_API.value == "rest_api"
        assert TargetType.MVC_WEB.value == "mvc_web"
        assert TargetType.GRAPHQL.value == "graphql"
        assert TargetType.ECOMMERCE.value == "ecommerce"
        assert TargetType.ADMIN_PANEL.value == "admin_panel"
        assert TargetType.UNKNOWN.value == "unknown"

    def test_string_mixin_serializes_as_plain_value(self):
        assert TargetType.SPA == "spa"
        assert f"{TargetType.REST_API.value}" == "rest_api"

    def test_constructs_from_string_value(self):
        assert TargetType("mvc_web") is TargetType.MVC_WEB

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            TargetType("not_a_real_target_type")


class TestBusinessValue:
    """Section 3 (LOW/MEDIUM/HIGH) + Section 11.3's R-L4 fix (UNKNOWN) =
    4 members. Week 3 addition; see docs/DECISIONS.md item 27 and this
    module's own docstring for why 4, not Section 3's literal 3.
    """

    def test_has_exactly_four_members(self):
        """Not 3 -- Section 11.3's deserialization fallback
        (`BusinessValue.UNKNOWN`) has no member to construct without
        this 4th one; see docs/DECISIONS.md item 27."""
        assert len(BusinessValue) == 4

    def test_values_match_section_3_and_11_3(self):
        assert BusinessValue.LOW.value == "low"
        assert BusinessValue.MEDIUM.value == "medium"
        assert BusinessValue.HIGH.value == "high"
        assert BusinessValue.UNKNOWN.value == "unknown"

    def test_string_mixin_serializes_as_plain_value(self):
        assert BusinessValue.HIGH == "high"
        assert f"{BusinessValue.LOW.value}" == "low"

    def test_constructs_from_string_value(self):
        assert BusinessValue("medium") is BusinessValue.MEDIUM

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            BusinessValue("not_a_real_business_value")

    def test_section_11_3_deserialization_fallback_pattern(self):
        """Pins the exact R-L4 pattern Week 4 will rely on: constructing
        from an unrecognized stored string raises ValueError, and the
        catch recovers to BusinessValue.UNKNOWN -- the reason this 4th
        member exists at all."""
        raw_value = "some_corrupted_checkpoint_string"
        try:
            result = BusinessValue(raw_value)
        except ValueError:
            result = BusinessValue.UNKNOWN
        assert result is BusinessValue.UNKNOWN

    def test_unknown_is_distinct_from_the_three_scoring_outcomes(self):
        """info_gain_scorer.py's scoring function (Section 6.7) must
        never produce UNKNOWN -- it is reserved for Week 4's
        deserialization fallback only (this module's docstring)."""
        scoring_outcomes = {BusinessValue.LOW, BusinessValue.MEDIUM, BusinessValue.HIGH}
        assert BusinessValue.UNKNOWN not in scoring_outcomes
        assert len(scoring_outcomes) == 3


class TestInteractshMode:
    """docs/DECISIONS.md item 81 -- consolidated here from
    scripts/interactsh_setup.py's own prior local definition."""

    def test_has_exactly_three_members(self):
        assert len(InteractshMode) == 3

    def test_members_match_section_4_1_and_4_3(self):
        assert InteractshMode.PUBLIC == "public"
        assert InteractshMode.SELF_HOSTED == "self_hosted"
        assert InteractshMode.UNAVAILABLE == "unavailable"

    def test_is_a_str_enum(self):
        """Matches every other enum in this file -- JSON-serializes as
        a plain string, same convention as BusinessValue/TargetType/etc."""
        assert isinstance(InteractshMode.PUBLIC, str)

    def test_survives_a_json_round_trip(self):
        import json

        value = InteractshMode.SELF_HOSTED
        restored = InteractshMode(json.loads(json.dumps(value.value)))
        assert restored is value


class TestOOBPollOutcome:
    def test_has_exactly_three_members(self):
        assert len(OOBPollOutcome) == 3

    def test_members_match_section_4_2_and_4_3(self):
        assert OOBPollOutcome.RECEIVED == "received"
        assert OOBPollOutcome.UNAVAILABLE == "unavailable"
        assert OOBPollOutcome.ENV_DEPENDENT == "env_dependent"

    def test_is_a_str_enum(self):
        assert isinstance(OOBPollOutcome.RECEIVED, str)

    def test_distinct_type_from_interactsh_mode_despite_shared_unavailable_string(self):
        """Both enums have an UNAVAILABLE-shaped member for different
        reasons (module docstring in interactsh_client.py: session-wide
        deployment state vs. one probe's own timeout) -- confirms they
        remain genuinely separate enum TYPES. Does NOT assert `!=`:
        `str, Enum` members compare equal across different enum classes
        whenever their underlying string values match (falls through to
        `str.__eq__`, ignoring enum identity) -- standard Python
        behavior this codebase already relies on throughout (every enum
        in this file is `str, Enum` for exactly this JSON-friendliness),
        not a bug introduced here. First written with a `!=` assertion,
        which failed against real Python semantics, not assumed --
        corrected to test the type distinction that's actually true and
        actually meaningful, rather than a value-equality claim that
        isn't."""
        assert OOBPollOutcome.UNAVAILABLE.value == InteractshMode.UNAVAILABLE.value
        assert type(OOBPollOutcome.UNAVAILABLE) is not type(InteractshMode.UNAVAILABLE)
        assert OOBPollOutcome.UNAVAILABLE is not InteractshMode.UNAVAILABLE
