"""
Implements: Section 3 -- core/chain/chain_engine.py ("NetworkX;
ChainBudgetEnforcer"). Also implements Section 12's Week 5 row
(`ChainExecutionEngine`, this class's name) and Section 6.1's
`[CHAIN DISCOVERY]` session-lifecycle stage at the data-structure level.
Blueprint: bb_agent_v6.6_final_blueprint.md

SELF-CONTAINED GRAPH, NOT `AttackGraph`/`AttackEdge` (`core/ontology/surface.py`,
UNBUILT): Section 3 lists `AttackEdge`/`AttackGraph` under `surface.py`,
produced by Fast Lane reconnaissance ("ReconSubgraph -> FastLane:
SurfaceData + AttackGraph", Section 8.1) and built by
`attack_graph_builder.py` -- itself unspecified beyond a bare filename
and explicitly NOT built this week (docs/DECISIONS.md, Week 5 tool-ify
scoping entry, same treatment as `core/tools/`'s four files). Section
8.3's `CHAIN_BROKEN -> Try alternate path in AttackGraph` describes
`AttackGraph` as the SEARCH SPACE a chain attempt draws alternate paths
from -- a different, larger structure than the specific, budget-gated,
in-progress chains this engine tracks. `ChainExecutionEngine` therefore
manages its own `networkx.DiGraph` (the same "self-contained graph, no
upstream ontology dependency" shape `core/cognitive/belief_manager.py`
already used for `BeliefGraph`, Week 4), not one built from
`AttackGraph`/`AttackEdge`. Once `attack_graph_builder.py` exists, wiring
this engine to search within a real `AttackGraph` is a natural follow-up
-- not assumed or stubbed toward here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from core.chain.chain_budget import ChainBudgetEnforcer


@dataclass
class ChainExecutionEngine:
    """Manages one session's in-progress exploit chains as a single
    `networkx.DiGraph`, budget-gated by `ChainBudgetEnforcer`.

    A "chain" here is a sequence of nodes reachable from a starting
    node, tagged with a shared `chain_id` graph attribute -- multiple
    chains may coexist in the same underlying `graph` (their nodes and
    edges are simply tagged differently), matching `BeliefGraph`'s own
    "one graph, node-attribute-distinguished concepts" shape (Week 4)
    rather than one `networkx.DiGraph` instance per chain.

    DEPTH IS TRACKED PER NODE, NOT PER CHAIN (fixed during this week's
    own testing -- caught by this file's own test suite, not shipped
    silently): a chain can branch (Section 8.3's `CHAIN_BROKEN -> Try
    alternate path` cascade implies exactly this -- trying a different
    next step from an already-confirmed point). An earlier version of
    this class tracked one depth counter per `chain_id`, incremented on
    every `extend_chain` call regardless of which node the extension
    came from; that made a "wide" chain (several children of the same
    node) exhaust `MAX_DEPTH` as fast as a "deep" one, which is wrong --
    two siblings of the same parent are both at the parent's depth + 1,
    not at increasingly greater depths of their own. `_node_depths` now
    keys by node identifier: `extend_chain` reads `from_node_id`'s own
    depth, checks the budget against THAT, and only the new node gets
    `from_depth + 1`. `chain_depth(chain_id)` (the chain-level view) is
    now derived: the maximum depth across every node currently tagged
    with that `chain_id`.

    Attributes:
        budget: The `ChainBudgetEnforcer` this engine consults before
            starting or extending any chain.
        graph: The underlying `networkx.DiGraph`. Node/edge attribute
            shapes are caller-defined (`**node_attrs` on `start_chain`/
            `extend_chain`) -- Section 3 gives no node-attribute schema
            for chain nodes the way it did for `BeliefGraph`'s (Section
            11.2), so none is assumed here.
    """

    budget: ChainBudgetEnforcer = field(default_factory=ChainBudgetEnforcer)
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)
    _node_depths: dict[object, int] = field(default_factory=dict, init=False)
    _chain_node_counts: dict[str, int] = field(default_factory=dict, init=False)

    def start_chain(self, chain_id: str, initial_node_id: str, **node_attrs: object) -> None:
        """Starts a new chain with one node, at depth 0.

        Args:
            chain_id: A unique identifier for this chain, stored as a
                `chain_id` node attribute so multiple chains can share
                one `graph`.
            initial_node_id: The starting node's identifier (this
                engine's own graph node key -- unrelated to any
                `ExploitCandidate`/`Finding` identifier scheme, which
                remain unspecified, docs/DECISIONS.md item 53).
            **node_attrs: Additional attributes stored on the node.

        Raises:
            TotalChainBudgetExhausted: If `MAX_TOTAL_CHAINS` (50) is
                already reached (`ChainBudgetEnforcer.register_new_chain`).
        """
        self.budget.register_new_chain()
        self.graph.add_node(initial_node_id, chain_id=chain_id, **node_attrs)
        self._node_depths[initial_node_id] = 0
        self._chain_node_counts[chain_id] = 1

    def extend_chain(self, chain_id: str, from_node_id: str, to_node_id: str, **node_attrs: object) -> bool:
        """Attempts to add one more node, as a child of `from_node_id`,
        to an existing chain.

        Args:
            chain_id: The chain being extended (must already exist via
                `start_chain`).
            from_node_id: The existing node this extension originates
                from. Its OWN depth (not the chain's node count, and
                not a chain-wide counter) determines whether this
                extension is within `MAX_DEPTH` -- two different
                extensions from the same `from_node_id` are siblings,
                both at `from_node_id`'s depth + 1, not at increasing
                depths of their own.
            to_node_id: The new node's identifier.
            **node_attrs: Additional attributes stored on the new node.

        Returns:
            `True` if the node was added. `False` -- not an exception --
            if extending would exceed `MAX_DEPTH`/`MAX_NODES_PER_CHAIN`
            (see `chain_budget.py`'s module docstring on why this is a
            bool, not a raise: a per-chain limit is a "try an alternate
            path" signal, Section 8.3's `CHAIN_BROKEN` cascade).
        """
        from_depth = self._node_depths.get(from_node_id, 0)
        node_count = self._chain_node_counts.get(chain_id, 0)
        if not self.budget.can_extend(from_depth, node_count):
            return False

        self.graph.add_node(to_node_id, chain_id=chain_id, **node_attrs)
        self.graph.add_edge(from_node_id, to_node_id)
        self._node_depths[to_node_id] = from_depth + 1
        self._chain_node_counts[chain_id] = node_count + 1
        return True

    def node_depth(self, node_id: object) -> int:
        """A specific node's own depth (0 if untracked)."""
        return self._node_depths.get(node_id, 0)

    def chain_depth(self, chain_id: str) -> int:
        """`chain_id`'s current depth: the maximum depth across every
        node currently tagged with it (0 if it doesn't exist, or has
        only its depth-0 root)."""
        node_ids = self.chain_node_ids(chain_id)
        if not node_ids:
            return 0
        return max(self._node_depths.get(n, 0) for n in node_ids)

    def chain_node_count(self, chain_id: str) -> int:
        """Current node count of `chain_id` (0 if it doesn't exist)."""
        return self._chain_node_counts.get(chain_id, 0)

    def chain_node_ids(self, chain_id: str) -> list[object]:
        """All node identifiers currently tagged with `chain_id`, in the
        order `networkx` iterates them (insertion order, in practice)."""
        return [n for n, attrs in self.graph.nodes(data=True) if attrs.get("chain_id") == chain_id]
