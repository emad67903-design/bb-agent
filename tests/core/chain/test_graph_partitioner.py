"""
Implements: Section 3 test coverage -- core/chain/graph_partitioner.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import networkx as nx

from core.chain.graph_partitioner import DEFAULT_MAX_NODES_PER_SUBGRAPH, partition_graph


class TestPartitionGraph:
    def test_empty_graph_returns_empty_list(self):
        assert partition_graph(nx.DiGraph()) == []

    def test_single_component_under_cap_returns_one_subgraph(self):
        g = nx.DiGraph()
        g.add_edges_from([("a", "b"), ("b", "c")])
        result = partition_graph(g, max_nodes_per_subgraph=200)
        assert len(result) == 1
        assert result[0].number_of_nodes() == 3

    def test_no_subgraph_exceeds_the_cap(self):
        g = nx.DiGraph()
        for i in range(250):
            g.add_node(f"n{i}")
            if i > 0:
                g.add_edge(f"n{i - 1}", f"n{i}")
        result = partition_graph(g, max_nodes_per_subgraph=100)
        assert all(sg.number_of_nodes() <= 100 for sg in result)

    def test_oversized_component_split_into_expected_chunk_sizes(self):
        g = nx.DiGraph()
        for i in range(250):
            g.add_node(f"n{i}")
            if i > 0:
                g.add_edge(f"n{i - 1}", f"n{i}")
        result = partition_graph(g, max_nodes_per_subgraph=100)
        sizes = sorted(sg.number_of_nodes() for sg in result)
        assert sizes == [50, 100, 100]

    def test_all_nodes_preserved_across_subgraphs(self):
        g = nx.DiGraph()
        for i in range(250):
            g.add_node(f"n{i}")
            if i > 0:
                g.add_edge(f"n{i - 1}", f"n{i}")
        result = partition_graph(g, max_nodes_per_subgraph=100)
        all_nodes = set()
        for sg in result:
            all_nodes |= set(sg.nodes)
        assert all_nodes == set(g.nodes)

    def test_separate_components_never_merged_into_one_subgraph(self):
        g = nx.DiGraph()
        g.add_edge("x1", "x2")
        g.add_edge("y1", "y2")
        result = partition_graph(g, max_nodes_per_subgraph=200)
        assert len(result) == 2
        node_sets = [set(sg.nodes) for sg in result]
        assert {"x1", "x2"} in node_sets
        assert {"y1", "y2"} in node_sets

    def test_separate_small_components_not_combined_even_though_both_fit(self):
        """Partitioning is by weakly-connected component first (module
        docstring) -- two unrelated 5-node components both under the cap
        still come back as two subgraphs, not merged into one 10-node
        subgraph, since they were never connected in the first place."""
        g = nx.DiGraph()
        g.add_edges_from([("a1", "a2"), ("a2", "a3")])
        g.add_edges_from([("b1", "b2"), ("b2", "b3")])
        result = partition_graph(g, max_nodes_per_subgraph=200)
        assert len(result) == 2

    def test_returned_subgraphs_are_independent_copies_not_views(self):
        g = nx.DiGraph()
        g.add_edge("a", "b")
        result = partition_graph(g, max_nodes_per_subgraph=200)
        result[0].add_node("injected")
        assert "injected" not in g.nodes

    def test_edges_within_a_subgraph_are_preserved(self):
        g = nx.DiGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        result = partition_graph(g, max_nodes_per_subgraph=200)
        assert result[0].has_edge("a", "b")
        assert result[0].has_edge("b", "c")

    def test_edges_across_split_chunks_are_dropped(self):
        """Inherent to splitting a connected component into disjoint
        node sets (module docstring), not a separate bug: an edge whose
        two endpoints land in different output subgraphs cannot be
        represented in either one alone."""
        g = nx.DiGraph()
        for i in range(4):
            g.add_node(f"n{i}")
            if i > 0:
                g.add_edge(f"n{i - 1}", f"n{i}")
        result = partition_graph(g, max_nodes_per_subgraph=2)
        total_edges = sum(sg.number_of_edges() for sg in result)
        assert total_edges < g.number_of_edges()

    def test_default_cap_is_two_hundred(self):
        assert DEFAULT_MAX_NODES_PER_SUBGRAPH == 200

    def test_partitioning_is_deterministic_across_repeated_calls(self):
        """Sorted node ordering before slicing (module docstring) --
        same graph, same call, same result every time."""
        g = nx.DiGraph()
        for i in range(250):
            g.add_node(f"n{i:03d}")
            if i > 0:
                g.add_edge(f"n{i - 1:03d}", f"n{i:03d}")
        result_a = partition_graph(g, max_nodes_per_subgraph=100)
        result_b = partition_graph(g, max_nodes_per_subgraph=100)
        assert [sorted(sg.nodes) for sg in result_a] == [sorted(sg.nodes) for sg in result_b]

    def test_original_graph_is_not_modified(self):
        g = nx.DiGraph()
        g.add_edge("a", "b")
        original_node_count = g.number_of_nodes()
        partition_graph(g, max_nodes_per_subgraph=1)
        assert g.number_of_nodes() == original_node_count
