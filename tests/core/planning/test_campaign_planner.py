"""
Implements: Section 6.4 test coverage -- core/planning/campaign_planner.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from core.ontology.enums import TargetType
from core.ontology.mental_model import Assumption, MentalModel
from core.planning.campaign_planner import CampaignPlan, build_campaign_plan
from core.planning.target_adapter import TargetAdapter


def _mental_model() -> MentalModel:
    return MentalModel(
        business_purpose="test target",
        roles=["visitor"],
        trust_boundaries=[],
        assumptions=[Assumption(description="x", exploitability_score=0.5)],
    )


class TestBuildCampaignPlan:
    def test_orders_scanners_by_adjusted_weight_descending(self):
        plan = build_campaign_plan(
            mental_model=_mental_model(),
            target_adapter=TargetAdapter(target_type=TargetType.REST_API),
            base_scanner_weights={"low": 0.1, "high": 0.9, "mid": 0.5},
        )
        assert plan.ordered_scanner_ids == ["high", "mid", "low"]

    def test_scanner_weights_reflect_target_adapter_output(self):
        weights = {"xss_scanner": 0.65, "sqli_scanner": 0.55}
        plan = build_campaign_plan(
            mental_model=_mental_model(),
            target_adapter=TargetAdapter(),
            base_scanner_weights=weights,
        )
        # Identity adjustment this week (target_adapter's own provisional
        # behavior) -- pinned here at the campaign_planner boundary too,
        # so a future change to either module's behavior is caught by
        # whichever test suite actually breaks, not silently absorbed.
        assert plan.scanner_weights == weights

    def test_carries_the_mental_model_used_to_build_it(self):
        mm = _mental_model()
        plan = build_campaign_plan(
            mental_model=mm,
            target_adapter=TargetAdapter(),
            base_scanner_weights={"a": 0.5},
        )
        assert plan.mental_model is mm

    def test_returns_a_campaign_plan_instance(self):
        plan = build_campaign_plan(
            mental_model=_mental_model(),
            target_adapter=TargetAdapter(),
            base_scanner_weights={},
        )
        assert isinstance(plan, CampaignPlan)
        assert plan.ordered_scanner_ids == []
        assert plan.scanner_weights == {}
