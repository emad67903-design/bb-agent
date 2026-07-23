"""
Implements: Section 3 -- core/control/llm_budget_dashboard.py
  ("STANDARD calls (shared by chain+BA), DEEP calls, thinking tokens,
  input tokens, Groq calls, local calls")
Also implements: Section 2.5's ContextWindow "--- BUDGET ---" template
block, R-M1 fix (BlueAgent shown as a subset, not a separate cap).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 1 SCOPE (Section 12 Week 1 row: "budget dashboard (STANDARD/DEEP
shared counter + BlueAgent subset display)"): renders exactly the
"--- BUDGET ---" block of Section 2.5's ContextWindow template, reading
from a core/governance/token_throttler.py TokenThrottler instance. Does
NOT build the rest of ContextWindow (Phase/Strategy/MentalModel/
BeliefGraph/ActiveChain/PendingActions/RecentOutcomes/Safety sections,
Section 2.5's other blocks) -- those depend on components that don't
exist yet (MentalModelBuilder, BeliefGraph, ChainEngine, etc., Weeks
3-7), and `core/cognitive/context_window.py` itself (the file Section 3
attributes the FULL template to) is not named in any Section 12 week
either -- flagged as a further gap of the same class as
`token_throttler.py`/`BudgetProfile`, but not resolved here since
nothing in Week 1 needs the rest of ContextWindow, only the budget
block. See docs/DECISIONS.md Week 1 section.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.governance.token_throttler import TokenThrottler


@dataclass(frozen=True)
class BudgetSnapshot:
    """Immutable, testable snapshot of one TokenThrottler's state.

    Exists as a separate type from TokenThrottler itself so the
    rendering function below (`render_budget_section`) can be tested
    against fixed, hand-constructed values without needing to drive a
    live TokenThrottler through dozens of `record_*` calls -- and so a
    future caller could build a snapshot from something other than a
    TokenThrottler (e.g. a deserialized checkpoint) without this module
    caring.

    Attributes: mirror TokenThrottler's read-outs 1:1 (see that class
        for the Section citations behind each). `groq_strategy_max` is
        `int | None`: no section gives an explicit per-session cap for
        `groq_strategy_model` the way it does for `groq_report_model`'s
        40 (Section 2.5's own template shows `{groq_strat_max}` as an
        unfilled placeholder, not a number) -- `None` renders as an
        honest "no fixed cap" rather than a fabricated figure. See
        docs/DECISIONS.md Week 1 section.
    """

    standard_cap: int
    standard_calls_used: int
    standard_calls_chain_and_business_logic: int
    standard_calls_blue_agent: int
    deep_cap: int
    deep_calls_used: int
    thinking_tokens_used: int
    max_possible_thinking_tokens: int
    gemini_input_tokens_used: int
    gemini_input_token_cap: int
    groq_strategy_calls_used: int
    groq_strategy_max: int | None
    groq_report_calls_used: int
    groq_report_reserved: int
    local_calls_used: int


def build_budget_snapshot(throttler: TokenThrottler, groq_strategy_max: int | None = None) -> BudgetSnapshot:
    """Reads a TokenThrottler's current state into a BudgetSnapshot.

    Args:
        throttler: The session's TokenThrottler.
        groq_strategy_max: Optional configured session ceiling for
            `groq_strategy_model` calls, if the caller has one (e.g.
            from `llm_config.yaml` once that key exists). Defaults to
            None -- no section of the blueprint gives this a concrete
            number (see BudgetSnapshot's docstring).

    Returns:
        A BudgetSnapshot reflecting `throttler`'s state at the moment of
        the call (not a live view -- it will not update if `throttler`
        is mutated afterward).
    """
    return BudgetSnapshot(
        standard_cap=throttler.STANDARD_CAP,
        standard_calls_used=throttler.standard_calls_used,
        standard_calls_chain_and_business_logic=throttler.standard_calls_chain_and_business_logic,
        standard_calls_blue_agent=throttler.standard_calls_blue_agent,
        deep_cap=throttler.DEEP_CAP,
        deep_calls_used=throttler.deep_calls_used,
        thinking_tokens_used=throttler.total_thinking_tokens_used,
        max_possible_thinking_tokens=throttler.max_possible_thinking_tokens,
        gemini_input_tokens_used=throttler.gemini_input_tokens_used,
        gemini_input_token_cap=throttler.GEMINI_INPUT_TOKEN_CAP,
        groq_strategy_calls_used=throttler.groq_strategy_calls_used,
        groq_strategy_max=groq_strategy_max,
        groq_report_calls_used=throttler.groq_report_calls_used,
        groq_report_reserved=throttler.GROQ_REPORT_RESERVED,
        local_calls_used=throttler.local_calls_used,
    )


def render_budget_section(snapshot: BudgetSnapshot) -> str:
    """Renders Section 2.5's exact "--- BUDGET ---" template block.

    Reproduced from Section 2.5 for direct comparison:
        --- BUDGET ---
        Gemini STANDARD cap=120 [chain/BL:{chain_bl_calls} BA:{ba_calls}]: {std_calls_used}/120
        Gemini DEEP cap=10 [exploit-confirm]: {deep_calls_used}/10
        Gemini thinking tokens: {thinking_tokens_used}/1,887,680
        Gemini input tokens: {gemini_input_tokens_used}/1,048,576
        Groq strategy: {groq_strat_used}/{groq_strat_max} | Groq report: {groq_rpt_used}/40
        Local-7B: {local_calls}

    `{groq_strat_max}` renders as the literal string "no fixed cap" when
    `snapshot.groq_strategy_max` is None (see BudgetSnapshot's
    docstring) rather than inventing a number the blueprint never gives.

    Args:
        snapshot: A BudgetSnapshot, typically from build_budget_snapshot.

    Returns:
        The rendered 6-line block, comma-formatted for the large token
        counts exactly as Section 2.5's template shows them
        (e.g. "1,887,680").
    """
    groq_strat_max_display = str(snapshot.groq_strategy_max) if snapshot.groq_strategy_max is not None else "no fixed cap"
    lines = [
        "--- BUDGET ---",
        (
            f"Gemini STANDARD cap={snapshot.standard_cap} "
            f"[chain/BL:{snapshot.standard_calls_chain_and_business_logic} "
            f"BA:{snapshot.standard_calls_blue_agent}]: "
            f"{snapshot.standard_calls_used}/{snapshot.standard_cap}"
        ),
        (
            f"Gemini DEEP cap={snapshot.deep_cap} [exploit-confirm]: "
            f"{snapshot.deep_calls_used}/{snapshot.deep_cap}"
        ),
        f"Gemini thinking tokens: {snapshot.thinking_tokens_used:,}/{snapshot.max_possible_thinking_tokens:,}",
        f"Gemini input tokens: {snapshot.gemini_input_tokens_used:,}/{snapshot.gemini_input_token_cap:,}",
        (
            f"Groq strategy: {snapshot.groq_strategy_calls_used}/{groq_strat_max_display} | "
            f"Groq report: {snapshot.groq_report_calls_used}/{snapshot.groq_report_reserved}"
        ),
        f"Local-7B: {snapshot.local_calls_used}",
    ]
    return "\n".join(lines)
