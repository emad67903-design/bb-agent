"""
Implements: Section 3 test coverage -- core/chain/chain_engine.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import networkx as nx
import pytest

from core.chain.chain_budget import ChainBudgetEnforcer, TotalChainBudgetExhausted
from core.chain.chain_engine import ChainExecutionEngine


class TestStartChain:
    def test_adds_the_initial_node(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        assert "n1" in engine.graph.nodes

    def test_tags_node_with_chain_id(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        assert engine.graph.nodes["n1"]["chain_id"] == "c1"

    def test_stores_extra_node_attrs(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1", vuln_type="xss", endpoint="/a")
        assert engine.graph.nodes["n1"]["vuln_type"] == "xss"
        assert engine.graph.nodes["n1"]["endpoint"] == "/a"

    def test_initial_depth_is_zero(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        assert engine.chain_depth("c1") == 0

    def test_initial_node_count_is_one(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        assert engine.chain_node_count("c1") == 1

    def test_consults_budget_and_raises_when_exhausted(self):
        engine = ChainExecutionEngine()
        for i in range(50):
            engine.start_chain(f"c{i}", f"n{i}")
        with pytest.raises(TotalChainBudgetExhausted):
            engine.start_chain("c50", "n50")

    def test_shares_a_single_underlying_graph_across_chains(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        engine.start_chain("c2", "n2")
        assert engine.graph.number_of_nodes() == 2
        assert set(engine.graph.nodes) == {"n1", "n2"}


class TestExtendChain:
    def test_extension_within_budget_succeeds(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        assert engine.extend_chain("c1", "n1", "n2") is True

    def test_extension_adds_node_and_edge(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        engine.extend_chain("c1", "n1", "n2")
        assert "n2" in engine.graph.nodes
        assert engine.graph.has_edge("n1", "n2")

    def test_extension_increments_depth_and_node_count(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        engine.extend_chain("c1", "n1", "n2")
        assert engine.chain_depth("c1") == 1
        assert engine.chain_node_count("c1") == 2

    def test_extension_tags_new_node_with_same_chain_id(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        engine.extend_chain("c1", "n1", "n2")
        assert engine.graph.nodes["n2"]["chain_id"] == "c1"

    def test_extension_stores_extra_node_attrs(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        engine.extend_chain("c1", "n1", "n2", vuln_type="sqli")
        assert engine.graph.nodes["n2"]["vuln_type"] == "sqli"

    def test_extension_beyond_max_depth_returns_false_not_raise(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n0")
        node = "n0"
        for i in range(1, 5):  # 4 successful extensions -> depth 4 (MAX_DEPTH)
            assert engine.extend_chain("c1", node, f"n{i}") is True
            node = f"n{i}"
        # 5th extension would push depth to 5 -- must be refused, not raise
        result = engine.extend_chain("c1", node, "n5")
        assert result is False

    def test_extension_beyond_max_depth_does_not_mutate_graph(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n0")
        node = "n0"
        for i in range(1, 5):
            engine.extend_chain("c1", node, f"n{i}")
            node = f"n{i}"
        nodes_before = engine.graph.number_of_nodes()
        engine.extend_chain("c1", node, "n5")
        assert engine.graph.number_of_nodes() == nodes_before

    def test_extension_beyond_max_nodes_returns_false(self):
        """MAX_NODES_PER_CHAIN=10 alone, isolated from MAX_DEPTH, by
        starting fresh chains that each add only one hop (so depth never
        approaches 4) but reusing the SAME chain_id's node-count tracking
        via direct engine state manipulation is not accessible publicly
        -- instead this drives node_count up via a wide, not deep,
        chain shape: extend from the SAME origin node repeatedly, which
        keeps depth at 1 for every extension after the first."""
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n0")
        # 9 extensions from n0 directly -> node_count reaches 10, depth stays 1
        for i in range(1, 9):
            assert engine.extend_chain("c1", "n0", f"n{i}") is True
        assert engine.chain_node_count("c1") == 9
        assert engine.extend_chain("c1", "n0", "n9") is True
        assert engine.chain_node_count("c1") == 10
        # 10th extension: node_count already at MAX_NODES_PER_CHAIN (10)
        assert engine.extend_chain("c1", "n0", "n10") is False

    def test_extending_nonexistent_chain_id_starts_its_tracking_at_zero(self):
        """No explicit start_chain call for "ghost" -- extend_chain still
        works (depth/node_count default to 0 via .get(..., 0)), matching
        dict.get's documented default rather than raising KeyError."""
        engine = ChainExecutionEngine()
        result = engine.extend_chain("ghost", "x", "y")
        assert result is True
        assert engine.chain_depth("ghost") == 1

    def test_two_children_of_the_same_node_are_siblings_at_the_same_depth(self):
        """Direct regression pin for the branching-depth fix: extending
        TWICE from the same from_node_id produces two siblings, both at
        from_node_id's depth + 1 -- not two increasingly-deep nodes.
        Before the fix, this incorrectly advanced a single whole-chain
        depth counter on every extend_chain call regardless of which
        node it came from."""
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "root")
        engine.extend_chain("c1", "root", "child_a")
        engine.extend_chain("c1", "root", "child_b")
        assert engine.node_depth("root") == 0
        assert engine.node_depth("child_a") == 1
        assert engine.node_depth("child_b") == 1
        assert engine.chain_depth("c1") == 1  # max depth across the chain, not 2

    def test_a_deep_branch_and_a_shallow_branch_are_tracked_independently(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "root")
        # Deep branch: root -> a -> b -> c (depth 3)
        engine.extend_chain("c1", "root", "a")
        engine.extend_chain("c1", "a", "b")
        engine.extend_chain("c1", "b", "c")
        # Shallow branch: root -> x (depth 1), a sibling of "a"
        engine.extend_chain("c1", "root", "x")
        assert engine.node_depth("c") == 3
        assert engine.node_depth("x") == 1
        assert engine.chain_depth("c1") == 3  # driven by the deep branch

    def test_extending_from_a_deep_node_is_still_gated_by_that_nodes_own_depth(self):
        """A node at MAX_DEPTH cannot be extended further, even if other
        nodes in the same chain are shallower."""
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n0")
        node = "n0"
        for i in range(1, 5):  # reach depth 4 (MAX_DEPTH) along this path
            engine.extend_chain("c1", node, f"n{i}")
            node = f"n{i}"
        assert engine.node_depth(node) == 4
        assert engine.extend_chain("c1", node, "too_deep") is False
        # but a shallow sibling of an early node is still fine
        assert engine.extend_chain("c1", "n0", "shallow_sibling") is True
        assert engine.node_depth("shallow_sibling") == 1


class TestChainNodeIds:
    def test_returns_only_nodes_for_the_given_chain(self):
        engine = ChainExecutionEngine()
        engine.start_chain("c1", "n1")
        engine.extend_chain("c1", "n1", "n2")
        engine.start_chain("c2", "m1")
        assert set(engine.chain_node_ids("c1")) == {"n1", "n2"}
        assert set(engine.chain_node_ids("c2")) == {"m1"}

    def test_empty_for_unknown_chain(self):
        engine = ChainExecutionEngine()
        assert engine.chain_node_ids("nonexistent") == []


class TestDependencyInjection:
    def test_accepts_a_pre_built_graph(self):
        g = nx.DiGraph()
        engine = ChainExecutionEngine(graph=g)
        engine.start_chain("c1", "n1")
        assert engine.graph is g

    def test_accepts_a_shared_budget_enforcer(self):
        budget = ChainBudgetEnforcer()
        engine = ChainExecutionEngine(budget=budget)
        engine.start_chain("c1", "n1")
        assert budget.total_chains_started == 1
