"""
Implements: Section 3 / Section 2.5 / Section 6.7 test coverage --
core/ontology/mental_model.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from datetime import datetime, timezone

import pytest

from core.ontology.mental_model import Assumption, MentalModel


class TestAssumption:
    def test_holds_description_and_score(self):
        a = Assumption(description="cookies are httponly", exploitability_score=0.8)
        assert a.description == "cookies are httponly"
        assert a.exploitability_score == 0.8

    def test_is_frozen(self):
        a = Assumption(description="x", exploitability_score=0.5)
        with pytest.raises(AttributeError):
            a.exploitability_score = 0.9  # type: ignore[misc]

    def test_info_gain_scorer_reads_this_exact_field(self):
        """Section 6.7's formula reads exploitability_score directly off
        each assumption -- pin the field name/type this depends on."""
        assumptions = [
            Assumption(description="a", exploitability_score=0.2),
            Assumption(description="b", exploitability_score=0.9),
            Assumption(description="c", exploitability_score=0.5),
        ]
        assert max(a.exploitability_score for a in assumptions) == 0.9


class TestMentalModel:
    """Confirmed fields (business_purpose, roles, trust_boundaries,
    assumptions) cite Section 2.5's Context Window template directly;
    is_partial/pages_analyzed/built_at are PROVISIONAL, per
    docs/DECISIONS.md item 29 -- both tiers are tested, but only the
    first four are treated as settled in comments/docs elsewhere."""

    def test_confirmed_fields_match_context_window_template(self):
        mm = MentalModel(
            business_purpose="E-commerce storefront",
            roles=["anonymous visitor", "authenticated customer", "admin"],
            trust_boundaries=["unauthenticated -> authenticated", "customer -> admin"],
            assumptions=[Assumption(description="admin panel is IP-restricted", exploitability_score=0.7)],
        )
        assert mm.business_purpose == "E-commerce storefront"
        assert mm.roles == ["anonymous visitor", "authenticated customer", "admin"]
        assert mm.trust_boundaries == ["unauthenticated -> authenticated", "customer -> admin"]
        assert len(mm.assumptions) == 1
        assert mm.assumptions[0].exploitability_score == 0.7

    def test_provisional_fields_have_sensible_defaults(self):
        """A caller that only supplies the four confirmed fields (e.g.
        an early or partial build of MentalModelBuilder) must not be
        forced to also know about the provisional fields."""
        mm = MentalModel(business_purpose="p")
        assert mm.roles == []
        assert mm.trust_boundaries == []
        assert mm.assumptions == []
        assert mm.is_partial is False
        assert mm.pages_analyzed == 0
        assert mm.built_at is None

    def test_is_partial_supports_mental_model_partial_log_semantics(self):
        """docs/DECISIONS.md item 15: [MENTAL_MODEL_PARTIAL] is a real
        log line (Section 6.3's abort path) -- this field is what lets
        code branch on that fact without re-parsing logs."""
        mm = MentalModel(business_purpose="p", is_partial=True, pages_analyzed=3)
        assert mm.is_partial is True
        assert mm.pages_analyzed == 3

    def test_built_at_accepts_a_real_timestamp(self):
        now = datetime.now(timezone.utc)
        mm = MentalModel(business_purpose="p", built_at=now)
        assert mm.built_at == now

    def test_is_frozen(self):
        mm = MentalModel(business_purpose="p")
        with pytest.raises(AttributeError):
            mm.business_purpose = "changed"  # type: ignore[misc]

    def test_mutable_defaults_are_independent_across_instances(self):
        """dataclass field(default_factory=list) must not share a list
        between instances -- pins against the classic mutable-default
        bug reappearing here."""
        a = MentalModel(business_purpose="a")
        b = MentalModel(business_purpose="b")
        a.roles.append("should not leak")
        assert b.roles == []
