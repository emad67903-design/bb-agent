"""
Implements: Section 3 -- core/chain/graph_partitioner.py ("max 200
nodes/subgraph").
Blueprint: bb_agent_v6.6_final_blueprint.md

CHUNKING STRATEGY IS A DOCUMENTED JUDGMENT CALL, NOT A CITATION: Section
3 gives only the number (200 nodes/subgraph) -- no algorithm for HOW to
split a graph that exceeds it. This implementation partitions by weakly-
connected component first (so a chain's own subgraph is never split
across output subgraphs unless the single chain itself exceeds 200
nodes -- `MAX_NODES_PER_CHAIN` is 10, per `chain_budget.py`, so in
practice a single `ChainExecutionEngine` chain never approaches this cap;
splitting only matters once multiple chains/components are combined into
one graph), then simple sequential slicing for any component that still
exceeds the cap on its own. Sorted node ordering before slicing, for
deterministic, reproducible output across repeated calls on the same
graph -- assumes homogeneous, sortable node identifiers (this project's
node IDs are consistently strings so far: `BeliefGraph`'s `hypothesis_id`,
`ChainExecutionEngine`'s caller-supplied node IDs).
"""

from __future__ import annotations

import networkx as nx

DEFAULT_MAX_NODES_PER_SUBGRAPH = 200


def partition_graph(graph: nx.DiGraph, max_nodes_per_subgraph: int = DEFAULT_MAX_NODES_PER_SUBGRAPH) -> list[nx.DiGraph]:
    """Splits `graph` into subgraphs, none exceeding `max_nodes_per_subgraph`.

    Args:
        graph: The graph to partition. Not modified -- each returned
            subgraph is an independent copy (`Graph.subgraph(...).copy()`),
            not a view into `graph`.
        max_nodes_per_subgraph: The per-subgraph node cap. Defaults to
            200 (Section 3's literal number).

    Returns:
        A list of subgraphs, each with at most `max_nodes_per_subgraph`
        nodes. Empty if `graph` has no nodes. Edges between nodes placed
        in different subgraphs are dropped (each subgraph only contains
        edges between its own nodes) -- an inherent consequence of
        splitting a graph into disjoint node sets, not a separate design
        choice.
    """
    subgraphs: list[nx.DiGraph] = []
    for component in nx.weakly_connected_components(graph):
        component_nodes = sorted(component)
        for i in range(0, len(component_nodes), max_nodes_per_subgraph):
            chunk = component_nodes[i : i + max_nodes_per_subgraph]
            subgraphs.append(graph.subgraph(chunk).copy())
    return subgraphs
