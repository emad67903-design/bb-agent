"""
Implements: Section 2.5/8.4/9.3/R-M1 test coverage -- core/governance/token_throttler.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import pytest

from core.governance.token_throttler import DeepBudgetExhausted, StandardBudgetExhausted, TokenThrottler


class TestStandardCounterBasics:
    def test_starts_at_zero(self):
        t = TokenThrottler()
        assert t.standard_calls_used == 0

    def test_increments_on_record(self):
        t = TokenThrottler()
        t.record_standard_call(caller_id="chain_agent")
        assert t.standard_calls_used == 1

    def test_raises_when_cap_reached(self):
        t = TokenThrottler()
        for _ in range(TokenThrottler.STANDARD_CAP):
            t.record_standard_call(caller_id="chain_agent")
        assert t.standard_calls_used == 120
        with pytest.raises(StandardBudgetExhausted, match="120"):
            t.record_standard_call(caller_id="chain_agent")
        # a rejected call must not silently increment the counter
        assert t.standard_calls_used == 120

    def test_input_tokens_accumulate(self):
        t = TokenThrottler()
        t.record_standard_call(caller_id="chain_agent", input_tokens=500)
        t.record_standard_call(caller_id="business_logic_agent", input_tokens=1_500)
        assert t.gemini_input_tokens_used == 2_000


class TestRM1SingleStandardCounter:
    """R-M1: BlueAgent shares the SAME counter/cap as ChainAgent+BusinessLogicAgent
    -- this is one of the most heavily cross-referenced invariants in the
    blueprint (Section 2.5, 8.4, 13, 14, 15#4). These tests exist
    specifically to catch a regression back to a separate BlueAgent
    budget, which Section 13 says would allow 140 calls = 2.15M tokens,
    over the 2M cap."""

    def test_blue_agent_calls_count_against_the_same_cap(self):
        t = TokenThrottler()
        for _ in range(100):
            t.record_standard_call(caller_id="chain_agent")
        for _ in range(20):
            t.record_standard_call(caller_id="blue_agent")
        assert t.standard_calls_used == 120
        with pytest.raises(StandardBudgetExhausted):
            t.record_standard_call(caller_id="blue_agent")
        with pytest.raises(StandardBudgetExhausted):
            t.record_standard_call(caller_id="chain_agent")

    def test_there_is_no_separate_blue_agent_cap_reachable(self):
        """100 chain/BL calls exhaust 100/120 of the SHARED cap; BlueAgent
        then only has 20 slots left in that SAME counter -- not its own
        120."""
        t = TokenThrottler()
        for _ in range(100):
            t.record_standard_call(caller_id="chain_agent")
        for _ in range(20):
            t.record_standard_call(caller_id="blue_agent")
        with pytest.raises(StandardBudgetExhausted):
            t.record_standard_call(caller_id="blue_agent")  # would be call #121 overall

    def test_standard_calls_blue_agent_is_display_subset_not_a_cap(self):
        t = TokenThrottler()
        for _ in range(5):
            t.record_standard_call(caller_id="blue_agent")
        assert t.standard_calls_blue_agent == 5
        assert t.standard_calls_used == 5  # same counter, not a second one

    def test_chain_and_business_logic_subset_excludes_blue_agent(self):
        t = TokenThrottler()
        for _ in range(60):
            t.record_standard_call(caller_id="chain_agent")
        for _ in range(40):
            t.record_standard_call(caller_id="business_logic_agent")
        for _ in range(20):
            t.record_standard_call(caller_id="blue_agent")
        assert t.standard_calls_chain_and_business_logic == 100
        assert t.standard_calls_blue_agent == 20
        assert t.standard_calls_chain_and_business_logic + t.standard_calls_blue_agent == t.standard_calls_used

    def test_arbitrary_caller_ids_still_count_against_shared_cap(self):
        """caller_id is not validated against a fixed set (Week 1 doesn't
        build the real callers yet) -- any caller_id still hits the one
        shared counter."""
        t = TokenThrottler()
        t.record_standard_call(caller_id="some_future_caller")
        assert t.standard_calls_used == 1
        assert t.standard_calls_blue_agent == 0
        assert t.standard_calls_chain_and_business_logic == 1


class TestDeepCounter:
    def test_starts_at_zero(self):
        assert TokenThrottler().deep_calls_used == 0

    def test_increments_on_record(self):
        t = TokenThrottler()
        t.record_deep_call()
        assert t.deep_calls_used == 1

    def test_raises_when_cap_reached(self):
        t = TokenThrottler()
        for _ in range(TokenThrottler.DEEP_CAP):
            t.record_deep_call()
        assert t.deep_calls_used == 10
        with pytest.raises(DeepBudgetExhausted, match="10"):
            t.record_deep_call()
        assert t.deep_calls_used == 10

    def test_deep_and_standard_caps_are_fully_independent(self):
        t = TokenThrottler()
        for _ in range(120):
            t.record_standard_call(caller_id="chain_agent")
        with pytest.raises(StandardBudgetExhausted):
            t.record_standard_call(caller_id="chain_agent")
        # STANDARD exhaustion must not affect DEEP at all
        t.record_deep_call()
        assert t.deep_calls_used == 1

    def test_input_tokens_accumulate_across_standard_and_deep(self):
        t = TokenThrottler()
        t.record_standard_call(caller_id="chain_agent", input_tokens=1_000)
        t.record_deep_call(input_tokens=2_000)
        assert t.gemini_input_tokens_used == 3_000


class TestThinkingTokenArithmetic:
    """Section 8.4: 120*13,000 + 10*32,768 = 1,887,680 < 2,000,000."""

    def test_max_possible_thinking_tokens_matches_section_8_4(self):
        assert TokenThrottler().max_possible_thinking_tokens == 1_887_680

    def test_max_possible_is_derived_not_hardcoded(self):
        """Recomputes independently from the class constants, so this
        test fails if the constants and the derived value ever diverge."""
        t = TokenThrottler()
        assert t.max_possible_thinking_tokens == (
            TokenThrottler.STANDARD_CAP * TokenThrottler.STANDARD_THINKING_PER_CALL
            + TokenThrottler.DEEP_CAP * TokenThrottler.DEEP_THINKING_PER_CALL
        )

    def test_standard_thinking_tokens_used_scales_with_calls(self):
        t = TokenThrottler()
        for _ in range(10):
            t.record_standard_call(caller_id="chain_agent")
        assert t.standard_thinking_tokens_used == 10 * 13_000 == 130_000

    def test_deep_thinking_tokens_used_scales_with_calls(self):
        t = TokenThrottler()
        for _ in range(3):
            t.record_deep_call()
        assert t.deep_thinking_tokens_used == 3 * 32_768 == 98_304

    def test_total_at_full_budget_matches_1_887_680(self):
        t = TokenThrottler()
        for _ in range(120):
            t.record_standard_call(caller_id="chain_agent")
        for _ in range(10):
            t.record_deep_call()
        assert t.total_thinking_tokens_used == 1_887_680
        assert t.total_thinking_tokens_used < 2_000_000  # Section 8.4's own sanity check


class TestGroqStrategy:
    def test_uncapped(self):
        t = TokenThrottler()
        for _ in range(1_000):
            t.record_groq_strategy_call()
        assert t.groq_strategy_calls_used == 1_000  # never raises


class TestGroqReport:
    def test_never_raises_past_the_reserve(self):
        t = TokenThrottler()
        for _ in range(45):
            t.record_groq_report_call()  # 45 > 40, must not raise
        assert t.groq_report_calls_used == 45

    def test_reserve_exceeded_flag_false_at_and_below_40(self):
        t = TokenThrottler()
        for _ in range(40):
            t.record_groq_report_call()
        assert t.groq_report_calls_used == 40
        assert t.groq_report_reserve_exceeded is False

    def test_reserve_exceeded_flag_true_above_40(self):
        t = TokenThrottler()
        for _ in range(41):
            t.record_groq_report_call()
        assert t.groq_report_reserve_exceeded is True


class TestLocalCalls:
    def test_uncapped(self):
        t = TokenThrottler()
        for _ in range(500):
            t.record_local_call()
        assert t.local_calls_used == 500


class TestIndependentThrottlerInstances:
    def test_two_instances_do_not_share_state(self):
        """Regression guard: ClassVar constants must stay class-level and
        read-only in effect, while the mutable counters must be
        per-instance -- a dataclass field misconfigured as a mutable
        class-level default would leak state between sessions."""
        t1 = TokenThrottler()
        t2 = TokenThrottler()
        t1.record_standard_call(caller_id="chain_agent")
        t1.record_deep_call()
        t1.record_groq_report_call()
        assert t2.standard_calls_used == 0
        assert t2.deep_calls_used == 0
        assert t2.groq_report_calls_used == 0
