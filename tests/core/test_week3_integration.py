"""
Implements: end-to-end integration coverage across Week 3's full chain
-- core/mental_model/builder.py -> core/planning/campaign_planner.py
-> core/planning/info_gain_scorer.py.
Blueprint: bb_agent_v6.6_final_blueprint.md

Added during the Week 3 final-verification pass, per the checklist item
"CampaignPlanner -- confirm it still builds correctly using whatever
MentalModel builder.py now actually produces." Each module already has
its own isolated unit tests; this file exists specifically to catch a
shape mismatch that only shows up when a REAL builder.py-produced
MentalModel flows into the modules built earlier in the week, which no
single module's own test suite would ever exercise on its own.
"""

import asyncio
from unittest import mock

from core.mental_model.builder import build_mental_model
from core.ontology.mental_model import Assumption, MentalModel, PageSignals
from core.ontology.enums import BusinessValue
from core.planning.campaign_planner import build_campaign_plan
from core.planning.info_gain_scorer import score_mental_model
from core.planning.target_adapter import TargetAdapter


class _FakeCapture:
    def __init__(self, url: str, html: str = "<html></html>", status: int = 200):
        self.final_url = url
        self.html = html
        self.status_code = status


class _FakeBrowserTool:
    async def capture(self, url: str):
        return _FakeCapture(url)


def _build_mental_model_via_full_pipeline(final_exploitability_score: float) -> MentalModel:
    with (
        mock.patch(
            "core.mental_model.builder.trace_page",
            side_effect=lambda u, h, s: PageSignals(page_url=u),
        ),
        mock.patch(
            "core.mental_model.builder.boundary_identifier.identify_boundaries",
            return_value=MentalModel(
                business_purpose="p", roles=["r"], data_flows=["f"], trust_boundaries=["b"]
            ),
        ),
        mock.patch(
            "core.mental_model.builder.assumption_extractor.extract_assumptions",
            side_effect=lambda mm, **kw: MentalModel(
                business_purpose=mm.business_purpose,
                roles=mm.roles,
                data_flows=mm.data_flows,
                trust_boundaries=mm.trust_boundaries,
                assumptions=[Assumption(description="a", exploitability_score=0.0)],
            ),
        ),
        mock.patch(
            "core.mental_model.builder.exploitability_scorer.score_exploitability",
            side_effect=lambda mm, **kw: MentalModel(
                business_purpose=mm.business_purpose,
                roles=mm.roles,
                data_flows=mm.data_flows,
                trust_boundaries=mm.trust_boundaries,
                assumptions=[
                    Assumption(description="a", exploitability_score=final_exploitability_score)
                ],
            ),
        ),
    ):
        return asyncio.run(build_mental_model("https://target.test/", _FakeBrowserTool()))


class TestBuilderToCampaignPlannerIntegration:
    def test_campaign_plan_builds_from_a_real_builder_produced_mental_model(self):
        mm = _build_mental_model_via_full_pipeline(final_exploitability_score=0.65)
        plan = build_campaign_plan(mm, TargetAdapter(), {"xss_scanner": 0.65, "sqli_scanner": 0.55})
        assert plan.mental_model is mm
        assert plan.ordered_scanner_ids == ["xss_scanner", "sqli_scanner"]

    def test_mental_model_from_builder_has_every_field_campaign_plan_expects(self):
        """Guards against a future field rename/removal in either
        module going unnoticed because no single module's own tests
        would construct a MentalModel exactly the way builder.py
        does."""
        mm = _build_mental_model_via_full_pipeline(final_exploitability_score=0.5)
        assert mm.business_purpose and mm.roles and mm.data_flows and mm.trust_boundaries
        assert mm.assumptions and mm.pages_analyzed >= 1 and mm.built_at is not None


class TestBuilderToInfoGainScorerIntegration:
    def test_business_value_computed_correctly_from_pipeline_output(self):
        mm_high = _build_mental_model_via_full_pipeline(final_exploitability_score=0.9)
        assert score_mental_model(mm_high) == BusinessValue.HIGH

        mm_medium = _build_mental_model_via_full_pipeline(final_exploitability_score=0.5)
        assert score_mental_model(mm_medium) == BusinessValue.MEDIUM

        mm_low = _build_mental_model_via_full_pipeline(final_exploitability_score=0.1)
        assert score_mental_model(mm_low) == BusinessValue.LOW
