"""
Implements: Section 3 -- core/chain/chain_budget.py ("max_depth=4,
max_total=50, max_nodes=10"). Also implements Section 8.4's "=== CHAIN
BUDGET ===" block ("Max chains: 50 | Max depth: 4 | Max nodes/chain:
10").
Blueprint: bb_agent_v6.6_final_blueprint.md

TWO DIFFERENT FAILURE MODES FOR TWO DIFFERENT KINDS OF LIMIT, NOT AN
INCONSISTENCY: `register_new_chain` RAISES when `MAX_TOTAL_CHAINS` (50)
would be exceeded; `can_extend` (checked against `MAX_DEPTH`/
`MAX_NODES_PER_CHAIN`, 4/10) only ever returns a bool, never raises.
This mirrors the distinction Section 8.3's error-handling cascade
already draws: `CHAIN_BROKEN -> Try alternate path in AttackGraph` --
hitting a PER-CHAIN limit is exactly the kind of recoverable, "try
something else" event that cascade describes, not a fatal stop. Hitting
the SESSION-WIDE total-chains cap is a materially different event (the
whole chain-discovery budget for the session is spent), closer in kind
to `token_throttler.py`'s STANDARD/DEEP hard caps (which also raise) than
to a single blocked path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


class TotalChainBudgetExhausted(Exception):
    """Raised by `register_new_chain` when `MAX_TOTAL_CHAINS` (50, session-wide) is already reached."""


@dataclass
class ChainBudgetEnforcer:
    """Section 3/8.4's chain budget: max 50 chains per session, each at
    most 4 hops deep with at most 10 nodes.

    Tracks only the session-wide total-chains count internally
    (`_total_chains_started`) -- per-chain depth/node counts are the
    caller's own state (`ChainExecutionEngine` tracks them, since it's
    the thing actually building each chain's graph structure); this
    class's `can_extend` takes them as parameters rather than owning a
    second copy of per-chain state.
    """

    MAX_DEPTH: ClassVar[int] = 4
    MAX_TOTAL_CHAINS: ClassVar[int] = 50
    MAX_NODES_PER_CHAIN: ClassVar[int] = 10

    _total_chains_started: int = field(default=0, init=False)

    @property
    def total_chains_started(self) -> int:
        return self._total_chains_started

    def can_start_new_chain(self) -> bool:
        """`True` if one more chain can be started without exceeding `MAX_TOTAL_CHAINS`."""
        return self._total_chains_started < self.MAX_TOTAL_CHAINS

    def register_new_chain(self) -> None:
        """Records the start of one new chain.

        Raises:
            TotalChainBudgetExhausted: If `MAX_TOTAL_CHAINS` (50) is
                already reached.
        """
        if not self.can_start_new_chain():
            raise TotalChainBudgetExhausted(
                f"Cannot start a new chain: {self._total_chains_started}/{self.MAX_TOTAL_CHAINS} already started this session"
            )
        self._total_chains_started += 1

    def can_extend(self, current_depth: int, current_node_count: int) -> bool:
        """`True` if a chain currently at `current_depth`/
        `current_node_count` may have one more node added without
        exceeding `MAX_DEPTH`/`MAX_NODES_PER_CHAIN`.

        Args:
            current_depth: The chain's current depth (hops from its
                start).
            current_node_count: The chain's current total node count.

        Returns:
            `True` if extension is permitted. Never raises -- a
            per-chain limit hit is a "try an alternate path" signal
            (Section 8.3's `CHAIN_BROKEN` cascade), not a fatal error.
        """
        return current_depth < self.MAX_DEPTH and current_node_count < self.MAX_NODES_PER_CHAIN
