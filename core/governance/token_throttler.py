"""
Implements: Section 3 -- core/governance/token_throttler.py
  ("STANDARD counter: shared by ChainAgent, BusinessLogicAgent, AND
  BlueAgent (cap=120 total, not separate). DEEP counter:
  ExploitConfirmationAgent only (cap=10).")
Also implements: Section 2.5 (ContextWindow budget fields), Section 8.4
(budget enforcement), Section 9.3 (LLM model roles), R-M1 fix (single
STANDARD counter -- no separate BlueAgent counter).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 1 SCOPE (docs/DECISIONS.md item 9): no week explicitly names this
file in Section 12; it lands in Week 1 as the direct backing store for
Week 1's own "budget dashboard" deliverable. This module builds ONLY the
counters and their hard-cap enforcement for the STANDARD/DEEP Gemini
tiers (raise on exceed -- Section 1.3/8.4/9.3 call these "cap", a hard
number, not a soft target). Groq's `groq_report_model` tier is handled
differently: Section 6.10/8.3 describe exceeding its 40-call reserve as
a graceful-degradation PATH ("Fallback reporter uses local-7B; log
[REPORT_DEGRADED]"), not a hard stop -- so `record_groq_report_call`
never raises; it exposes `groq_report_reserve_exceeded` for the actual
caller (Week 7's `autonomous_reporter.py`) to check and react to. This
module does NOT build the fallback CASCADE itself (Section 8.3's
GEMINI_LIMIT_HIT -> Groq -> local-7B -> pause routing): no week's
Section 12 line item builds the actual Gemini/Groq/local calling code
yet (StrategicPlanner, MentalModelBuilder, ChainAgent,
BusinessLogicAgent, BlueAgent, ExploitConfirmationAgent each arrive in
their own later weeks), so there is nothing yet to route between.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


class StandardBudgetExhausted(Exception):
    """Raised when a STANDARD call is recorded after the cap (120) is already reached."""


class DeepBudgetExhausted(Exception):
    """Raised when a DEEP call is recorded after the cap (10) is already reached."""


@dataclass
class TokenThrottler:
    """Enforces Section 2.5/8.4/9.3's Gemini call-budget rules for one session.

    R-M1 fix (one of the most heavily cross-referenced invariants in the
    blueprint -- Section 2.5, 8.4, 13, 14, and guidance #4 in Section
    15): there is exactly ONE STANDARD counter, shared by ChainAgent,
    BusinessLogicAgent, and BlueAgent combined (cap 120). BlueAgent's
    calls are a SUBSET of that counter, tracked separately only for
    DISPLAY (`standard_calls_blue_agent`), never as an independent
    budget. A separate-counter design would allow 140 total STANDARD-class
    calls = 2.15M thinking tokens, over the 2M combined cap (Section 13's
    rejected items) -- this class makes that error structurally
    impossible: there is exactly one field (`_standard_calls`) ever
    compared against `STANDARD_CAP`; `_standard_calls_by_caller` is a
    read-only-in-effect breakdown of the SAME total, not a second cap.

    Attributes (class-level constants, Section 1.3/2.5/6.10/8.4/9.3):
        STANDARD_CAP: 120.
        DEEP_CAP: 10.
        STANDARD_THINKING_PER_CALL: 13,000.
        DEEP_THINKING_PER_CALL: 32,768.
        GEMINI_INPUT_TOKEN_CAP: 1,048,576 (gemini-2.5-pro's own per-call
            context window -- Section 2.5 calls it the "Gemini 2.5 Pro
            limit"). NOTE: the dashboard template (Section 2.5) displays
            a CUMULATIVE `gemini_input_tokens_used` against this same
            number, in the same budget block as the genuinely-cumulative
            STANDARD/DEEP/thinking-token metrics. That pairs a
            per-call ceiling with a cumulative counter -- a unit
            mismatch in the blueprint's own template, not introduced
            here. Implemented literally as specified (cumulative
            counter, this constant as the displayed denominator);
            flagged rather than silently "corrected." See
            docs/DECISIONS.md Week 1 section.
        GROQ_REPORT_RESERVED: 40 ("reserved in session plan", Section
            6.10/8.4/9.3) -- a soft reserve, not a hard cap; see class
            docstring above and `groq_report_reserve_exceeded`.
    """

    STANDARD_CAP: ClassVar[int] = 120
    DEEP_CAP: ClassVar[int] = 10
    STANDARD_THINKING_PER_CALL: ClassVar[int] = 13_000
    DEEP_THINKING_PER_CALL: ClassVar[int] = 32_768
    GEMINI_INPUT_TOKEN_CAP: ClassVar[int] = 1_048_576
    GROQ_REPORT_RESERVED: ClassVar[int] = 40

    _standard_calls: int = field(default=0, init=False)
    _standard_calls_by_caller: dict[str, int] = field(default_factory=dict, init=False)
    _deep_calls: int = field(default=0, init=False)
    _gemini_input_tokens: int = field(default=0, init=False)
    _groq_strategy_calls: int = field(default=0, init=False)
    _groq_report_calls: int = field(default=0, init=False)
    _local_calls: int = field(default=0, init=False)

    # --- recording ---

    def record_standard_call(self, caller_id: str, input_tokens: int = 0) -> None:
        """Records one STANDARD-tier Gemini call (ChainAgent/BusinessLogicAgent/BlueAgent).

        Args:
            caller_id: Identifies which component made the call (e.g.
                "chain_agent", "business_logic_agent", "blue_agent").
                Not validated against a fixed set -- Week 1 does not
                build those components, so their exact identifiers
                aren't fixed yet. Every `caller_id` increments the SAME
                counter against the SAME cap (R-M1); `caller_id` only
                affects the `standard_calls_blue_agent` /
                `standard_calls_chain_and_business_logic` DISPLAY
                breakdown, never enforcement.
            input_tokens: Input tokens this call consumed, added to the
                running cumulative total (see `GEMINI_INPUT_TOKEN_CAP`
                docstring note on the unit mismatch this reflects).

        Raises:
            StandardBudgetExhausted: If the STANDARD cap (120) is
                already reached.
        """
        if self._standard_calls >= self.STANDARD_CAP:
            raise StandardBudgetExhausted(
                f"STANDARD cap ({self.STANDARD_CAP}) already reached; cannot record another call"
            )
        self._standard_calls += 1
        self._standard_calls_by_caller[caller_id] = self._standard_calls_by_caller.get(caller_id, 0) + 1
        self._gemini_input_tokens += input_tokens

    def record_deep_call(self, input_tokens: int = 0) -> None:
        """Records one DEEP-tier Gemini call (ExploitConfirmationAgent only).

        Args:
            input_tokens: Input tokens this call consumed.

        Raises:
            DeepBudgetExhausted: If the DEEP cap (10) is already
                reached.
        """
        if self._deep_calls >= self.DEEP_CAP:
            raise DeepBudgetExhausted(f"DEEP cap ({self.DEEP_CAP}) already reached; cannot record another call")
        self._deep_calls += 1
        self._gemini_input_tokens += input_tokens

    def record_groq_strategy_call(self) -> None:
        """Records one Groq `groq_strategy_model` call (StrategicPlanner/MentalModelBuilder).

        No hard cap enforced: Section 9.3's 14,400/day figure is Groq's
        OWN external daily rate limit, not a per-session budget this
        project tracks call-by-call, and no section gives an explicit
        per-session number for this tier the way it does for
        `groq_report_model`'s 40 (see docs/DECISIONS.md Week 1 section
        on `groq_strat_max`). Uncapped by design at this layer.
        """
        self._groq_strategy_calls += 1

    def record_groq_report_call(self) -> None:
        """Records one Groq `groq_report_model` call (AutonomousReporter).

        Never raises -- see class docstring: exceeding the 40-call
        reserve is a graceful-degradation trigger (Section 6.10/8.3),
        not a hard stop. Callers check `groq_report_reserve_exceeded`
        and react (fall back to local-7B, log `[REPORT_DEGRADED]`)
        themselves; that reaction is Week 7's `autonomous_reporter.py`,
        not this module.
        """
        self._groq_report_calls += 1

    def record_local_call(self) -> None:
        """Records one local qwen2.5-coder:7b call. Unlimited (Section 9.3); never raises."""
        self._local_calls += 1

    # --- STANDARD/DEEP read-outs ---

    @property
    def standard_calls_used(self) -> int:
        """Total STANDARD-tier calls recorded so far (all callers combined)."""
        return self._standard_calls

    @property
    def standard_calls_blue_agent(self) -> int:
        """R-M1: BlueAgent's contribution to the single STANDARD counter, for display only."""
        return self._standard_calls_by_caller.get("blue_agent", 0)

    @property
    def standard_calls_chain_and_business_logic(self) -> int:
        """Section 2.5 template's '[chain/BL:...]' subset: everything in the
        single STANDARD counter that is NOT BlueAgent."""
        return self._standard_calls - self.standard_calls_blue_agent

    @property
    def deep_calls_used(self) -> int:
        return self._deep_calls

    @property
    def max_possible_thinking_tokens(self) -> int:
        """Section 2.5/8.4: `120*13,000 + 10*32,768 = 1,887,680`.

        Computed from STANDARD_CAP/DEEP_CAP/*_THINKING_PER_CALL rather
        than hardcoded, so it can never silently drift from the two
        caps it is derived from.
        """
        return self.STANDARD_CAP * self.STANDARD_THINKING_PER_CALL + self.DEEP_CAP * self.DEEP_THINKING_PER_CALL

    @property
    def standard_thinking_tokens_used(self) -> int:
        return self._standard_calls * self.STANDARD_THINKING_PER_CALL

    @property
    def deep_thinking_tokens_used(self) -> int:
        return self._deep_calls * self.DEEP_THINKING_PER_CALL

    @property
    def total_thinking_tokens_used(self) -> int:
        return self.standard_thinking_tokens_used + self.deep_thinking_tokens_used

    @property
    def gemini_input_tokens_used(self) -> int:
        return self._gemini_input_tokens

    # --- Groq / local read-outs ---

    @property
    def groq_strategy_calls_used(self) -> int:
        return self._groq_strategy_calls

    @property
    def groq_report_calls_used(self) -> int:
        return self._groq_report_calls

    @property
    def groq_report_reserve_exceeded(self) -> bool:
        """Section 6.10/8.3: True once calls exceed the 40-call reserve.

        Not an exception-raising condition (see `record_groq_report_call`)
        -- a signal for the caller to fall back to local-7B and log
        `[REPORT_DEGRADED]` (Week 7's `autonomous_reporter.py`).

        # TODO(Week7): autonomous_reporter.py must log `[REPORT_DEGRADED]`
        # at the call site where it checks this property and actually
        # falls back to local-7B (Section 6.10: "If Groq limit hit during
        # reporting: fallback to local 7B; log [REPORT_DEGRADED]"; Section
        # 8.3's GROQ_LIMIT_REPORTING cascade entry). Breadcrumbed here so
        # it isn't dropped by the time Week 7 arrives -- see
        # docs/DECISIONS.md Week 1 section, item 18.
        """
        return self._groq_report_calls > self.GROQ_REPORT_RESERVED

    @property
    def local_calls_used(self) -> int:
        return self._local_calls
