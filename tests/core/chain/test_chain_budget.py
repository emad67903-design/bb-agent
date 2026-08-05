"""
Implements: Section 3/8.4 test coverage -- core/chain/chain_budget.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import pytest

from core.chain.chain_budget import ChainBudgetEnforcer, TotalChainBudgetExhausted


class TestConstants:
    def test_matches_section_8_4_exactly(self):
        assert ChainBudgetEnforcer.MAX_DEPTH == 4
        assert ChainBudgetEnforcer.MAX_TOTAL_CHAINS == 50
        assert ChainBudgetEnforcer.MAX_NODES_PER_CHAIN == 10


class TestTotalChains:
    def test_starts_at_zero(self):
        assert ChainBudgetEnforcer().total_chains_started == 0

    def test_can_start_new_chain_true_below_cap(self):
        enforcer = ChainBudgetEnforcer()
        for _ in range(49):
            enforcer.register_new_chain()
        assert enforcer.can_start_new_chain() is True

    def test_can_start_new_chain_false_at_cap(self):
        enforcer = ChainBudgetEnforcer()
        for _ in range(50):
            enforcer.register_new_chain()
        assert enforcer.can_start_new_chain() is False

    def test_register_new_chain_increments_count(self):
        enforcer = ChainBudgetEnforcer()
        enforcer.register_new_chain()
        enforcer.register_new_chain()
        assert enforcer.total_chains_started == 2

    def test_register_new_chain_raises_once_cap_reached(self):
        enforcer = ChainBudgetEnforcer()
        for _ in range(50):
            enforcer.register_new_chain()
        with pytest.raises(TotalChainBudgetExhausted, match="50/50"):
            enforcer.register_new_chain()

    def test_raising_does_not_increment_count_further(self):
        enforcer = ChainBudgetEnforcer()
        for _ in range(50):
            enforcer.register_new_chain()
        with pytest.raises(TotalChainBudgetExhausted):
            enforcer.register_new_chain()
        assert enforcer.total_chains_started == 50


class TestCanExtend:
    def test_true_well_within_both_limits(self):
        assert ChainBudgetEnforcer().can_extend(current_depth=0, current_node_count=1) is True

    def test_true_at_depth_boundary_minus_one(self):
        assert ChainBudgetEnforcer().can_extend(current_depth=3, current_node_count=1) is True

    def test_false_at_depth_boundary(self):
        """MAX_DEPTH=4: depth already at 4 means no further extension."""
        assert ChainBudgetEnforcer().can_extend(current_depth=4, current_node_count=1) is False

    def test_false_past_depth_boundary(self):
        assert ChainBudgetEnforcer().can_extend(current_depth=5, current_node_count=1) is False

    def test_true_at_node_count_boundary_minus_one(self):
        assert ChainBudgetEnforcer().can_extend(current_depth=0, current_node_count=9) is True

    def test_false_at_node_count_boundary(self):
        """MAX_NODES_PER_CHAIN=10: node count already at 10 means no further extension."""
        assert ChainBudgetEnforcer().can_extend(current_depth=0, current_node_count=10) is False

    def test_false_when_either_limit_hit_even_if_other_is_fine(self):
        assert ChainBudgetEnforcer().can_extend(current_depth=4, current_node_count=1) is False
        assert ChainBudgetEnforcer().can_extend(current_depth=0, current_node_count=10) is False

    def test_never_raises(self):
        """A per-chain limit is a bool signal, never an exception (module docstring)."""
        result = ChainBudgetEnforcer().can_extend(current_depth=999, current_node_count=999)
        assert result is False  # returned, not raised

    def test_independent_of_total_chains_state(self):
        """can_extend is purely a function of the two arguments given --
        it must not be affected by how many chains have been started."""
        enforcer = ChainBudgetEnforcer()
        for _ in range(50):
            enforcer.register_new_chain()
        assert enforcer.can_extend(current_depth=0, current_node_count=1) is True
