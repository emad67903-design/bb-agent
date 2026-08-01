"""
Implements: mental_model_builder_prompt_design.md Section 4 test
coverage -- core/mental_model/assumption_extractor.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import pytest

from core.mental_model._groq_client import GroqCallError, GroqCallResult
from core.mental_model.assumption_extractor import extract_assumptions
from core.ontology.mental_model import MentalModel


def _mm() -> MentalModel:
    return MentalModel(
        business_purpose="An e-commerce storefront.",
        roles=["anonymous", "customer", "admin"],
        data_flows=["checkout form -> payment gateway"],
        trust_boundaries=["anonymous -> customer", "customer -> admin"],
    )


class TestExtractAssumptions:
    def test_populates_assumptions_from_groq_response(self, mocker, tmp_path):
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mocker.patch(
            "core.mental_model.assumption_extractor.call_groq_json",
            return_value=GroqCallResult(
                parsed={
                    "assumptions": [
                        {"description": "price is re-validated server-side"},
                        {"description": "admin panel checks role server-side"},
                    ]
                },
                raw_content="{...}",
            ),
        )
        result = extract_assumptions(_mm(), llm_config_path=llm_config, api_key="k")
        assert len(result.assumptions) == 2
        assert result.assumptions[0].description == "price is re-validated server-side"
        assert result.assumptions[0].exploitability_score == 0.0

    def test_preserves_call_1_fields_unchanged(self, mocker, tmp_path):
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mocker.patch(
            "core.mental_model.assumption_extractor.call_groq_json",
            return_value=GroqCallResult(
                parsed={"assumptions": [{"description": "x"}]}, raw_content="{}"
            ),
        )
        mm = _mm()
        result = extract_assumptions(mm, llm_config_path=llm_config, api_key="k")
        assert result.business_purpose == mm.business_purpose
        assert result.roles == mm.roles
        assert result.data_flows == mm.data_flows
        assert result.trust_boundaries == mm.trust_boundaries

    def test_empty_assumptions_list_raises(self, mocker, tmp_path):
        """mental_model_builder_prompt_design.md Section 4: minItems:1
        is deliberate -- info_gain_scorer.py can't score zero
        assumptions."""
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mocker.patch(
            "core.mental_model.assumption_extractor.call_groq_json",
            return_value=GroqCallResult(parsed={"assumptions": []}, raw_content="{}"),
        )
        with pytest.raises(GroqCallError, match="empty assumptions list"):
            extract_assumptions(_mm(), llm_config_path=llm_config, api_key="k")

    def test_sends_call_1_fields_into_prompt(self, mocker, tmp_path):
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mock_call = mocker.patch(
            "core.mental_model.assumption_extractor.call_groq_json",
            return_value=GroqCallResult(
                parsed={"assumptions": [{"description": "x"}]}, raw_content="{}"
            ),
        )
        extract_assumptions(_mm(), llm_config_path=llm_config, api_key="k")
        prompt = mock_call.call_args.kwargs["user_prompt"]
        assert "An e-commerce storefront." in prompt
        assert "checkout form -> payment gateway" in prompt

    def test_context_fields_are_delimiter_wrapped(self, mocker, tmp_path):
        """item 42 correction: call-1 output crosses a call boundary
        into this prompt and is wrapped, same as boundary_identifier.py's
        own PageSignals wrapping."""
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mock_call = mocker.patch(
            "core.mental_model.assumption_extractor.call_groq_json",
            return_value=GroqCallResult(
                parsed={"assumptions": [{"description": "x"}]}, raw_content="{}"
            ),
        )
        extract_assumptions(_mm(), llm_config_path=llm_config, api_key="k")
        prompt = mock_call.call_args.kwargs["user_prompt"]
        assert "<<<DATA>>>An e-commerce storefront.<<<END DATA>>>" in prompt
