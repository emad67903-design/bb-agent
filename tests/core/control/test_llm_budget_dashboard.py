"""
Implements: Section 2.5/3 test coverage -- core/control/llm_budget_dashboard.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import pytest

from core.control.llm_budget_dashboard import BudgetSnapshot, build_budget_snapshot, render_budget_section
from core.governance.token_throttler import TokenThrottler


class TestBuildBudgetSnapshot:
    def test_zero_state_throttler(self):
        snapshot = build_budget_snapshot(TokenThrottler())
        assert snapshot.standard_cap == 120
        assert snapshot.standard_calls_used == 0
        assert snapshot.standard_calls_chain_and_business_logic == 0
        assert snapshot.standard_calls_blue_agent == 0
        assert snapshot.deep_cap == 10
        assert snapshot.deep_calls_used == 0
        assert snapshot.thinking_tokens_used == 0
        assert snapshot.max_possible_thinking_tokens == 1_887_680
        assert snapshot.gemini_input_tokens_used == 0
        assert snapshot.gemini_input_token_cap == 1_048_576
        assert snapshot.groq_strategy_calls_used == 0
        assert snapshot.groq_strategy_max is None
        assert snapshot.groq_report_calls_used == 0
        assert snapshot.groq_report_reserved == 40
        assert snapshot.local_calls_used == 0

    def test_populated_throttler_reflected_correctly(self):
        t = TokenThrottler()
        for _ in range(60):
            t.record_standard_call(caller_id="chain_agent", input_tokens=100)
        for _ in range(40):
            t.record_standard_call(caller_id="business_logic_agent", input_tokens=100)
        for _ in range(15):
            t.record_standard_call(caller_id="blue_agent", input_tokens=100)
        for _ in range(4):
            t.record_deep_call(input_tokens=500)
        for _ in range(5):
            t.record_groq_strategy_call()
        for _ in range(12):
            t.record_groq_report_call()
        for _ in range(300):
            t.record_local_call()

        snapshot = build_budget_snapshot(t, groq_strategy_max=5)

        assert snapshot.standard_calls_used == 115
        assert snapshot.standard_calls_chain_and_business_logic == 100
        assert snapshot.standard_calls_blue_agent == 15
        assert snapshot.deep_calls_used == 4
        assert snapshot.thinking_tokens_used == 115 * 13_000 + 4 * 32_768
        assert snapshot.gemini_input_tokens_used == 115 * 100 + 4 * 500
        assert snapshot.groq_strategy_calls_used == 5
        assert snapshot.groq_strategy_max == 5
        assert snapshot.groq_report_calls_used == 12
        assert snapshot.local_calls_used == 300

    def test_snapshot_is_frozen(self):
        snapshot = build_budget_snapshot(TokenThrottler())
        with pytest.raises(Exception):
            snapshot.standard_calls_used = 999  # type: ignore[misc]

    def test_snapshot_does_not_update_after_throttler_mutates(self):
        t = TokenThrottler()
        snapshot = build_budget_snapshot(t)
        t.record_standard_call(caller_id="chain_agent")
        assert snapshot.standard_calls_used == 0  # point-in-time, not live


class TestRenderBudgetSection:
    def test_zero_state_matches_section_2_5_template_exactly(self):
        snapshot = build_budget_snapshot(TokenThrottler())
        rendered = render_budget_section(snapshot)
        expected = (
            "--- BUDGET ---\n"
            "Gemini STANDARD cap=120 [chain/BL:0 BA:0]: 0/120\n"
            "Gemini DEEP cap=10 [exploit-confirm]: 0/10\n"
            "Gemini thinking tokens: 0/1,887,680\n"
            "Gemini input tokens: 0/1,048,576\n"
            "Groq strategy: 0/no fixed cap | Groq report: 0/40\n"
            "Local-7B: 0"
        )
        assert rendered == expected

    def test_full_budget_state_matches_expected_rendering(self):
        t = TokenThrottler()
        for _ in range(100):
            t.record_standard_call(caller_id="chain_agent")
        for _ in range(20):
            t.record_standard_call(caller_id="blue_agent")
        for _ in range(10):
            t.record_deep_call()
        for _ in range(5):
            t.record_groq_strategy_call()
        for _ in range(40):
            t.record_groq_report_call()
        for _ in range(287):
            t.record_local_call()

        snapshot = build_budget_snapshot(t)
        rendered = render_budget_section(snapshot)
        expected = (
            "--- BUDGET ---\n"
            "Gemini STANDARD cap=120 [chain/BL:100 BA:20]: 120/120\n"
            "Gemini DEEP cap=10 [exploit-confirm]: 10/10\n"
            "Gemini thinking tokens: 1,887,680/1,887,680\n"
            "Gemini input tokens: 0/1,048,576\n"
            "Groq strategy: 5/no fixed cap | Groq report: 40/40\n"
            "Local-7B: 287"
        )
        assert rendered == expected

    def test_groq_strategy_max_renders_as_number_when_supplied(self):
        snapshot = build_budget_snapshot(TokenThrottler(), groq_strategy_max=5)
        rendered = render_budget_section(snapshot)
        assert "Groq strategy: 0/5 |" in rendered

    def test_large_token_counts_are_comma_formatted(self):
        t = TokenThrottler()
        for _ in range(120):
            t.record_standard_call(caller_id="chain_agent", input_tokens=10_000)
        snapshot = build_budget_snapshot(t)
        rendered = render_budget_section(snapshot)
        assert "1,560,000/1,887,680" in rendered  # thinking tokens at 120 STANDARD, 0 DEEP
        assert "1,200,000/1,048,576" in rendered  # input tokens (exceeds cap on purpose -- rendering doesn't clip)

    def test_blue_agent_subset_and_total_are_both_shown_and_consistent(self):
        t = TokenThrottler()
        for _ in range(70):
            t.record_standard_call(caller_id="chain_agent")
        for _ in range(30):
            t.record_standard_call(caller_id="blue_agent")
        snapshot = build_budget_snapshot(t)
        rendered = render_budget_section(snapshot)
        assert "[chain/BL:70 BA:30]: 100/120" in rendered

    def test_render_is_pure_function_of_snapshot(self):
        """Constructing a BudgetSnapshot directly (no TokenThrottler at
        all) must render identically -- proves render_budget_section
        depends only on the dataclass, not on TokenThrottler internals."""
        snapshot = BudgetSnapshot(
            standard_cap=120,
            standard_calls_used=5,
            standard_calls_chain_and_business_logic=5,
            standard_calls_blue_agent=0,
            deep_cap=10,
            deep_calls_used=0,
            thinking_tokens_used=65_000,
            max_possible_thinking_tokens=1_887_680,
            gemini_input_tokens_used=0,
            gemini_input_token_cap=1_048_576,
            groq_strategy_calls_used=1,
            groq_strategy_max=None,
            groq_report_calls_used=0,
            groq_report_reserved=40,
            local_calls_used=10,
        )
        rendered = render_budget_section(snapshot)
        assert "Gemini STANDARD cap=120 [chain/BL:5 BA:0]: 5/120" in rendered
        assert "Gemini thinking tokens: 65,000/1,887,680" in rendered
