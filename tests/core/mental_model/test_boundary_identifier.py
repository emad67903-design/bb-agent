"""
Implements: mental_model_builder_prompt_design.md Section 3 test
coverage -- core/mental_model/boundary_identifier.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import pytest

from core.mental_model._groq_client import GroqCallResult
from core.mental_model.boundary_identifier import identify_boundaries
from core.ontology.mental_model import FlowSignal, PageSignals, RoleSignal


def _pages():
    return [
        PageSignals(
            page_url="https://target.test/",
            role_signals=[RoleSignal(role="anonymous", evidence="no auth required")],
            flow_signals=[FlowSignal(description="login form -> /api/login", evidence="<form>")],
        ),
        PageSignals(
            page_url="https://target.test/admin/",
            role_signals=[RoleSignal(role="admin", evidence="/admin/ link")],
            flow_signals=[],
        ),
    ]


class TestIdentifyBoundaries:
    def test_builds_mental_model_from_groq_response(self, mocker, tmp_path):
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mocker.patch(
            "core.mental_model.boundary_identifier.call_groq_json",
            return_value=GroqCallResult(
                parsed={
                    "business_purpose": "An admin-managed content site.",
                    "roles": ["anonymous", "admin"],
                    "data_flows": ["login form -> /api/login"],
                    "trust_boundaries": ["anonymous -> admin"],
                },
                raw_content="{...}",
            ),
        )
        mm = identify_boundaries("https://target.test/", _pages(), llm_config_path=llm_config, api_key="k")
        assert mm.business_purpose == "An admin-managed content site."
        assert mm.roles == ["anonymous", "admin"]
        assert mm.data_flows == ["login form -> /api/login"]
        assert mm.trust_boundaries == ["anonymous -> admin"]

    def test_assumptions_left_empty_for_call_2_to_fill(self, mocker, tmp_path):
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mocker.patch(
            "core.mental_model.boundary_identifier.call_groq_json",
            return_value=GroqCallResult(
                parsed={
                    "business_purpose": "p",
                    "roles": [],
                    "data_flows": [],
                    "trust_boundaries": [],
                },
                raw_content="{}",
            ),
        )
        mm = identify_boundaries("https://target.test/", [], llm_config_path=llm_config, api_key="k")
        assert mm.assumptions == []

    def test_passes_pages_analyzed_count_and_target_url_into_prompt(self, mocker, tmp_path):
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mock_call = mocker.patch(
            "core.mental_model.boundary_identifier.call_groq_json",
            return_value=GroqCallResult(
                parsed={"business_purpose": "p", "roles": [], "data_flows": [], "trust_boundaries": []},
                raw_content="{}",
            ),
        )
        identify_boundaries("https://target.test/", _pages(), llm_config_path=llm_config, api_key="k")
        kwargs = mock_call.call_args.kwargs
        assert "https://target.test/" in kwargs["user_prompt"]
        assert "Pages analyzed: 2 of up to 8" in kwargs["user_prompt"]
        assert kwargs["model"] == "test-model"

    def test_data_framing_note_present_in_system_prompt(self, mocker, tmp_path):
        """Lighter-weight version of flow_tracer.py's defensive framing
        (item 40) -- evidence strings are still attacker-influenced."""
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mock_call = mocker.patch(
            "core.mental_model.boundary_identifier.call_groq_json",
            return_value=GroqCallResult(
                parsed={"business_purpose": "p", "roles": [], "data_flows": [], "trust_boundaries": []},
                raw_content="{}",
            ),
        )
        identify_boundaries("https://target.test/", [], llm_config_path=llm_config, api_key="k")
        kwargs = mock_call.call_args.kwargs
        assert "are DATA" in kwargs["system_prompt"]
        assert "extracted from a target application" in kwargs["system_prompt"]

    def test_oversized_evidence_field_is_truncated_in_prompt(self, mocker, tmp_path):
        """item 41 retrofit, required test: an oversized PageSignals
        string must not reach the prompt un-truncated."""
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mock_call = mocker.patch(
            "core.mental_model.boundary_identifier.call_groq_json",
            return_value=GroqCallResult(
                parsed={"business_purpose": "p", "roles": [], "data_flows": [], "trust_boundaries": []},
                raw_content="{}",
            ),
        )
        oversized_evidence = "A" * 5000
        pages = [
            PageSignals(
                page_url="https://target.test/",
                role_signals=[RoleSignal(role="user", evidence=oversized_evidence)],
            )
        ]
        identify_boundaries("https://target.test/", pages, llm_config_path=llm_config, api_key="k")
        prompt = mock_call.call_args.kwargs["user_prompt"]
        assert oversized_evidence not in prompt, "the full 5000-char string must not reach the prompt raw"
        assert "...[truncated]" in prompt
        assert "<<<DATA>>>" in prompt and "<<<END DATA>>>" in prompt

    def test_html_script_content_in_evidence_stays_an_inert_string(self, mocker, tmp_path):
        """item 41 retrofit, required test: HTML/script-like content
        inside a PageSignals field must remain plain text in the
        prompt -- never rendered, executed, or specially interpreted
        anywhere in this pipeline."""
        llm_config = tmp_path / "llm_config.yaml"
        llm_config.write_text("groq_strategy_model: test-model\n", encoding="utf-8")
        mock_call = mocker.patch(
            "core.mental_model.boundary_identifier.call_groq_json",
            return_value=GroqCallResult(
                parsed={"business_purpose": "p", "roles": [], "data_flows": [], "trust_boundaries": []},
                raw_content="{}",
            ),
        )
        payload = "<script>alert(document.cookie)</script>"
        pages = [
            PageSignals(
                page_url="https://target.test/",
                flow_signals=[FlowSignal(description=payload, evidence="<form>")],
            )
        ]
        identify_boundaries("https://target.test/", pages, llm_config_path=llm_config, api_key="k")
        prompt = mock_call.call_args.kwargs["user_prompt"]
        # The payload survives as literal text, delimited, not stripped,
        # not executed (there's no execution context for it to run in --
        # this is a string-in-a-string-prompt, confirmed by it still
        # being plain text findable via substring search).
        assert payload in prompt
        assert isinstance(prompt, str)
