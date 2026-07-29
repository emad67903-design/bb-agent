"""
Implements: Section 6.7 test coverage -- core/planning/info_gain_scorer.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import pytest

from core.ontology.enums import BusinessValue
from core.ontology.mental_model import Assumption, MentalModel
from core.planning.info_gain_scorer import score_assumptions, score_mental_model


def _assumption(score: float) -> Assumption:
    return Assumption(description="x", exploitability_score=score)


class TestScoreAssumptions:
    """Section 6.7, exact thresholds: HIGH if max>=0.7, MEDIUM if
    0.4<=max<0.7, LOW if max<0.4. Boundary values pinned exactly, not
    just interior examples -- this project's own standard for
    threshold arithmetic."""

    def test_high_at_exactly_0_7(self):
        assert score_assumptions([_assumption(0.7)]) == BusinessValue.HIGH

    def test_high_above_0_7(self):
        assert score_assumptions([_assumption(0.95)]) == BusinessValue.HIGH

    def test_medium_just_below_0_7(self):
        assert score_assumptions([_assumption(0.6999)]) == BusinessValue.MEDIUM

    def test_medium_at_exactly_0_4(self):
        assert score_assumptions([_assumption(0.4)]) == BusinessValue.MEDIUM

    def test_medium_interior(self):
        assert score_assumptions([_assumption(0.55)]) == BusinessValue.MEDIUM

    def test_low_just_below_0_4(self):
        assert score_assumptions([_assumption(0.3999)]) == BusinessValue.LOW

    def test_low_at_zero(self):
        assert score_assumptions([_assumption(0.0)]) == BusinessValue.LOW

    def test_uses_the_maximum_not_the_average_or_first(self):
        """Section 6.7 says max(...) explicitly -- one high-scoring
        assumption among many low ones must still produce HIGH."""
        assumptions = [_assumption(0.1), _assumption(0.2), _assumption(0.9), _assumption(0.15)]
        assert score_assumptions(assumptions) == BusinessValue.HIGH

    def test_never_returns_unknown(self):
        """BusinessValue.UNKNOWN is Week 4's deserialization fallback
        only (docs/DECISIONS.md item 27) -- no score, however extreme,
        should produce it here."""
        for score in (0.0, 0.39, 0.4, 0.69, 0.7, 1.0):
            assert score_assumptions([_assumption(score)]) != BusinessValue.UNKNOWN

    def test_empty_assumptions_raises(self):
        with pytest.raises(ValueError, match="at least one assumption"):
            score_assumptions([])


class TestScoreMentalModel:
    def test_scores_all_assumptions_in_the_model(self):
        mm = MentalModel(
            business_purpose="p",
            assumptions=[_assumption(0.1), _assumption(0.85)],
        )
        assert score_mental_model(mm) == BusinessValue.HIGH

    def test_empty_model_assumptions_raises(self):
        mm = MentalModel(business_purpose="p", assumptions=[])
        with pytest.raises(ValueError, match="at least one assumption"):
            score_mental_model(mm)
