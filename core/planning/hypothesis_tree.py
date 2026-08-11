"""
Implements: Section 3 -- core/planning/hypothesis_tree.py (bare filename,
zero spec -- docs/DECISIONS.md item 64). Section 8.3's
"HYPOTHESIS_FALSE -> Generate alternative hypothesis" cascade line is the
only concrete blueprint anchor for this module's content.
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE, RATIFIED (docs/DECISIONS.md item 64's Section 39 architectural
review, approved): a structural RELATIONSHIP LAYER over the existing
BeliefGraph, not a second graph, not a class, not a store of its own.
Relationships are plain edge attributes on the SAME `networkx.DiGraph`
`belief_manager.py` already owns -- empirically verified during review
that `serialize_belief_graph`/`deserialize_belief_graph` (Section 11.3,
Week 4, closed) round-trip arbitrary edge attributes with ZERO code
changes, and that `prune_graph` (Week 4, closed) already drops a pruned
node's incident relationship edges automatically, for free, via
`networkx.DiGraph.remove_nodes_from`'s own standard behavior. Neither
function is touched by this file.

FUNCTIONS OVER A CLASS, DELIBERATELY -- same shape as
`core/cognitive/belief_manager.py` (docs/DECISIONS.md item 44's
reasoning applied identically here): the blueprint never specifies a
`class HypothesisTree`, and this module owns no state of its own --
every function takes the caller's `graph: nx.DiGraph` first and mutates
or reads it directly, exactly like `belief_manager.py`'s `seed_node`/
`update_node`/`get_probability`.

ONLY `ALTERNATIVE_TO`, NOT `DERIVED_FROM`/`SUPPORTS`/`CONTRADICTS`
(review Section 35, reconfirmed): grepped the blueprint twice,
independently, across two review passes. Zero occurrences of any of the
three anywhere. "Alternative" itself appears exactly once, at Section
8.3. Per the review's own Section 21 rule ("every API must have a
concrete consumer, a concrete call site, a clear invariant, and a
test") -- the other three relation types have none of the four and are
not built here. Revisit only if/when the blueprint or a future
`DECISIONS.md` item gives one of them an actual citation and consumer.

EDGE DIRECTION CONVENTION: `add_alternative_relationship(graph,
alternative_id, original_id)` adds the edge `alternative_id ->
original_id`, read as "alternative_id IS alternative_to original_id" --
a directed predicate, subject-to-object, matching how the relation name
itself reads. This is a documented convention, not a blueprint
citation; `get_alternatives_of`/`is_alternative_of` below are written
against this exact direction and would need updating together if it
ever changes.

RELATIONSHIPS ONLY EVER CONNECT ALREADY-COMMITTED HYPOTHESES: this
module never creates a BeliefGraph node itself (that remains
`hypothesis_engine.seed_hypothesis`'s -> `belief_manager.add_belief_node`'s
job, unchanged). `add_alternative_relationship` requires both node IDs
to already exist in `graph` and raises `KeyError` otherwise -- this is
the concrete enforcement of the review's LLM-trust-boundary requirement
(Section 19: candidate -> validation -> deterministic commit ->
BeliefGraph) applied to relationships specifically: a relationship
cannot be the FIRST thing written for a hypothesis that was never
itself validated and committed.

`relation_type` IS A PLAIN STRING, NOT AN ENUM (review Section E,
deliberate): `serialize_belief_graph` special-cases exactly three node
fields (`last_updated`, `pinned_until`, `business_value`) and touches
nothing else, node or edge. A raw-enum edge attribute would need the
same `.value`-before-`json.dumps()` / `try-except`-on-load treatment
`business_value` already gets -- which means editing
`core/cognitive/belief_manager.py`, a closed, independently-verified
Week 4 file this review's own Final Contract explicitly forbids
touching. A plain string sidesteps that entirely: confirmed empirically
during review (an edge with `relation_type="alternative_to"` round-
tripped through the unmodified serializer with the string intact).
"""

from __future__ import annotations

from datetime import datetime, timezone

import networkx as nx

# Section 8.3's only named hypothesis-relationship concept. A module-level
# constant, not an enum -- see module docstring, "`relation_type` IS A
# PLAIN STRING, NOT AN ENUM" -- so there is exactly one legal value for
# now, referenced here rather than typed as a literal string at each
# call site.
ALTERNATIVE_TO = "alternative_to"


def add_alternative_relationship(
    graph: nx.DiGraph,
    alternative_id: str,
    original_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Records that `alternative_id` is an alternative hypothesis to
    `original_id` (Section 8.3: "HYPOTHESIS_FALSE -> Generate
    alternative hypothesis").

    Adds one directed edge, `alternative_id -> original_id`, carrying
    `relation_type=ALTERNATIVE_TO` and `created_at` (see module
    docstring, "EDGE DIRECTION CONVENTION"). Both node IDs must already
    be committed `BeliefGraph` nodes (via
    `hypothesis_engine.seed_hypothesis` -> `belief_manager.add_belief_node`)
    -- this function never creates a node.

    Args:
        graph: The `networkx.DiGraph` containing both `alternative_id`
            and `original_id` as existing nodes.
        alternative_id: The newly-seeded alternative hypothesis's node
            ID (the edge's source).
        original_id: The falsified hypothesis's node ID (the edge's
            target).
        now: Timestamp recorded as `created_at` (ISO-8601 string).
            Defaults to `datetime.now(timezone.utc)` if not supplied;
            overridable for deterministic tests -- same convention
            `belief_manager.py`'s own `now` parameters use.

    Raises:
        KeyError: If either `alternative_id` or `original_id` is not
            already a node in `graph`. Relationships only ever connect
            already-committed hypotheses (module docstring).
    """
    if alternative_id not in graph.nodes:
        raise KeyError(f"add_alternative_relationship: {alternative_id!r} is not an existing node in graph")
    if original_id not in graph.nodes:
        raise KeyError(f"add_alternative_relationship: {original_id!r} is not an existing node in graph")

    timestamp = now if now is not None else datetime.now(timezone.utc)
    graph.add_edge(
        alternative_id,
        original_id,
        relation_type=ALTERNATIVE_TO,
        created_at=timestamp.isoformat(),
    )


def get_alternatives_of(graph: nx.DiGraph, hypothesis_id: str) -> list[str]:
    """All hypothesis IDs recorded as alternatives to `hypothesis_id`.

    Reads in-edges tagged `relation_type=ALTERNATIVE_TO` whose target
    is `hypothesis_id` (module docstring, "EDGE DIRECTION CONVENTION":
    `alternative_id -> original_id`, so alternatives are `hypothesis_id`'s
    predecessors, not successors).

    Args:
        graph: The `networkx.DiGraph` to read.
        hypothesis_id: The (possibly falsified) hypothesis to look up
            alternatives for. Need not exist in `graph` -- an absent or
            childless node simply has no alternatives.

    Returns:
        A list of hypothesis IDs, in `networkx`'s predecessor iteration
        order (insertion order, in practice). Empty list, never an
        exception, if `hypothesis_id` has no recorded alternatives or
        does not exist in `graph` (review Section F: "Empty list, not
        exception, if none").
    """
    if hypothesis_id not in graph.nodes:
        return []
    return [
        predecessor
        for predecessor in graph.predecessors(hypothesis_id)
        if graph.edges[predecessor, hypothesis_id].get("relation_type") == ALTERNATIVE_TO
    ]


def is_alternative_of(graph: nx.DiGraph, candidate_id: str, original_id: str) -> bool:
    """`True` if `candidate_id` is recorded as an alternative to `original_id`.

    A direct edge-existence-and-type check (module docstring, "EDGE
    DIRECTION CONVENTION": `candidate_id -> original_id`).

    Args:
        graph: The `networkx.DiGraph` to read.
        candidate_id: The hypothesis being checked as a possible
            alternative.
        original_id: The hypothesis it may be an alternative to.

    Returns:
        `True` if the edge `candidate_id -> original_id` exists with
        `relation_type=ALTERNATIVE_TO`. `False` otherwise, including
        when either ID is absent from `graph` -- never raises.
    """
    if not graph.has_edge(candidate_id, original_id):
        return False
    return graph.edges[candidate_id, original_id].get("relation_type") == ALTERNATIVE_TO
