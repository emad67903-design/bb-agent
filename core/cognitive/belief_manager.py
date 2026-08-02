"""
Implements: Section 11 -- Belief Graph Scalability (core/cognitive/
belief_manager.py). Also implements Section 3's belief_manager.py tree
comment ("max_nodes=15000; pruning_threshold=0.05... Node attributes:
alpha, beta (int), exploitability_score, business_value (BusinessValue
enum), pinned_until (datetime|None)... seed_alpha_beta(w) ->
(round(w*10), 10-round(w*10))"), and Section 2 Layer 3's `BeliefManager`
component line ("Bayesian BeliefGraph updates").
Blueprint: bb_agent_v6.6_final_blueprint.md

API SHAPE -- FUNCTIONS OVER A CLASS, DELIBERATELY (docs/DECISIONS.md item
44): the blueprint never defines a `class BeliefGraph` or a `BeliefManager`
class anywhere -- grepped across the full document, `BeliefManager` only
ever appears as a component-name label (Section 2 Layer 3's bullet list;
Section 8.4's local-7B call-budget line, "BeliefManager node updates:
~50"), never as a class with methods. Section 11.2's own code block is
unambiguous about the actual API shape: every function
(`seed_node`, `update_node`, `get_probability`) takes a bare `graph`
parameter first and operates on it directly -- a functional module over
`networkx.DiGraph`, not an object wrapping one. This file matches that
shape exactly rather than inventing a `BeliefManager` class the blueprint
never specifies a single method signature for.

`seed_node` NAME AND SHAPE FOLLOW SECTION 11.2, NOT SECTION 3'S
`seed_alpha_beta` (docs/DECISIONS.md item 44): Section 3's tree comment
names a different function -- `seed_alpha_beta(w) -> (round(w*10),
10-round(w*10))` -- single parameter, tuple return. Section 11.2 gives
`seed_node(vuln_type: str, w: float) -> dict` with a two-key dict return,
plus a worked example (`w=0.65 -> alpha=7, beta=3`) that docs/DECISIONS.md
item 4 and this week's kickoff both single out for pinning as a test.
Resolved the same way docs/DECISIONS.md item 25 already established for
this exact class of disagreement: the full code block is authoritative
over the abbreviated tree comment. No second, differently-shaped
`seed_alpha_beta` function is added alongside it -- nothing in the
blueprint calls that name from anywhere else, so a second function would
be dead code motivated only by the tree comment's abbreviation of the
same concept.

`round(w * 10)` USES ROUND-HALF-UP, NOT PYTHON'S BUILT-IN `round()`
(docs/DECISIONS.md item 46, CRITICAL): Section 11.2's code and its own
adjacent worked example directly contradict each other under a literal
reading. The code says `a = round(w * 10)`; the worked example two lines
below it says `w=0.65 -> alpha=7, beta=3`. Python's built-in `round()`
uses round-half-to-even ("banker's rounding"): `round(6.5) == 6` (6 is
even), not 7 -- confirmed by direct execution, not assumed, and not a
floating-point representation artifact (`0.65 * 10 == 6.5` exactly, no
drift). A literal transcription of `round(w * 10)` using Python's
built-in therefore produces `{'alpha': 6, 'beta': 4}` for the blueprint's
own headline example, contradicting the `✓` the blueprint places next to
`alpha=7, beta=3`. This is a Category (A) internal contradiction (two
adjacent pieces of the same code block disagree, not two separate
sections) -- flagged per the STOP-condition protocol, resolved rather
than halted, because the resolution direction is unusually well
evidenced: (1) the worked example is the one Section 11.2 explicitly
marks correct with `✓`; (2) this week's kickoff instructions independently
single out `w=0.65 -> alpha=7, beta=3` for pinning as a test, twice, with
no mention of 6/4; (3) checked against all ten distinct values actually
used in `vuln_weights.yaml`'s `starting_weights` (Section 9.5), three
(0.25, 0.45, 0.65) diverge between the two rounding rules -- this is not
a one-off corner case affecting only the example, it would silently
mis-seed ~30% of the 29 vuln types' cold-start alpha/beta pairs by
exactly 1 if left as Python's default `round()`. Implemented below via
`math.floor(w * 10 + 0.5)` (standard round-half-up for the non-negative
domain Section 11.2 itself specifies for `w`, "[0.0, 1.0]").

DATETIME CONVENTION -- TIMEZONE-AWARE, NOT SECTION 11.2's LITERAL
`datetime.utcnow()` (docs/DECISIONS.md item 48): Section 11.2's
`update_node` body literally calls `datetime.utcnow()` (naive). This
module uses `datetime.now(timezone.utc)` (aware) instead, matching the
convention already established twice elsewhere in this codebase --
`core/governance/scope_config_generator.py:158` and
`core/mental_model/builder.py:260` (`MentalModel.built_at`) both already
use `datetime.now(timezone.utc)`. Mixing naive and aware datetimes in the
same process raises `TypeError` on comparison (`pinned_until > now`,
Section 11.2's pruning rules); since `pinned_until` on a BeliefGraph node
and `MentalModel.built_at` may plausibly need comparing against the same
"now" in future code, staying on the codebase's already-established aware
convention is the lower-risk reading of "current UTC time" than a literal
transcription that would reintroduce a naive/aware split this project has
twice already avoided. Callers of `add_belief_node` must supply aware
datetimes for `pinned_until`; the round-trip tests below pin this.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import networkx as nx

from core.ontology.enums import BusinessValue

logger = logging.getLogger(__name__)


# Section 11.1, transcribed verbatim.
BELIEF_GRAPH_LIMITS: dict[str, int | float] = {
    "max_nodes": 15_000,
    "max_edges": 100_000,
    "pruning_threshold": 0.05,
    "pruning_interval": 300,  # seconds
}

# Section 11.2's "Pruning:" bullet list gives four rules as prose with
# inline numbers, not as a named dict the way Section 11.1 gives
# BELIEF_GRAPH_LIMITS. Pulled into named module-level constants here
# per the Engineering Constitution's "CONFIG-DRIVEN, ZERO MAGIC NUMBERS"
# mandate, rather than left as inline literals in `should_prune`.
#
# Rule 1's probability threshold is NOT a new constant: it is the same
# 0.05 as BELIEF_GRAPH_LIMITS["pruning_threshold"] (Section 11.1) --
# reused directly below rather than redeclared, since the two numbers
# being identical is evidently not a coincidence (Section 11.1 names the
# graph-wide pruning threshold; Section 11.2's first rule is the one rule
# that actually uses it). The remaining three numbers (10 min, 0.15, 60
# min, 0.8) have no equivalent named home anywhere in Section 11.1.
PRUNE_LOW_PROBABILITY_STALE_MINUTES = 10
PRUNE_MODERATE_PROBABILITY_THRESHOLD = 0.15
PRUNE_MODERATE_STALE_MINUTES = 60
NEVER_PRUNE_EXPLOITABILITY_THRESHOLD = 0.8


def new_belief_graph() -> nx.DiGraph:
    """Construct an empty BeliefGraph.

    A `networkx.DiGraph` whose nodes carry Section 11.2's nine-key
    attribute dict (`hypothesis_id`, `vuln_type`, `endpoint`, `alpha`,
    `beta`, `exploitability_score`, `business_value`, `pinned_until`,
    `last_updated`) once populated via `add_belief_node`. This function
    itself is a thin, explicit "start here" wrapper around `nx.DiGraph()`
    -- Section 11 never names edge attributes, so no edge schema is
    assumed or enforced.

    Returns:
        A new, empty `networkx.DiGraph`.
    """
    return nx.DiGraph()


def seed_node(vuln_type: str, w: float) -> dict[str, int]:
    """Seed alpha/beta for a fresh BeliefGraph node from a starting weight.

    Transcribed verbatim from Section 11.2, including the unused
    `vuln_type` parameter -- the blueprint's own one-line definition
    accepts it without using it in the arithmetic (module docstring,
    "`seed_node` NAME AND SHAPE..."), and this function matches that
    literally rather than silently dropping or repurposing the parameter.

    Args:
        vuln_type: The vulnerability type this node will represent.
            Unused by this function's own arithmetic; accepted because
            Section 11.2 declares it in the signature.
        w: Starting weight in [0.0, 1.0], typically read from
            `vuln_weights.yaml`'s `starting_weights` (Section 9.5).

    Returns:
        A dict with exactly two keys, `'alpha'` and `'beta'`, both `int`,
        summing to exactly 10.

    Example:
        Section 11.2's own worked example, pinned as
        `test_seed_node_matches_section_11_2_worked_example`:
        `seed_node("xss", 0.65)` == `{'alpha': 7, 'beta': 3}` (mean =
        7/10 = 0.70 ~ the 0.65 prior).
    """
    # Round-half-up, not Python's built-in round() (banker's rounding) --
    # see module docstring, "`round(w * 10)` USES ROUND-HALF-UP...".
    # math.floor(x + 0.5) is exact and safe here because w is documented
    # (Section 11.2) as always in [0.0, 1.0], so w * 10 is always in
    # [0.0, 10.0] -- non-negative, where this idiom has no edge cases.
    a = math.floor(w * 10 + 0.5)
    return {"alpha": a, "beta": 10 - a}


def add_belief_node(
    graph: nx.DiGraph,
    *,
    hypothesis_id: str,
    vuln_type: str,
    endpoint: str,
    starting_weight: float,
    business_value: BusinessValue,
    exploitability_score: float = 0.5,
    pinned_until: datetime | None = None,
    now: datetime | None = None,
) -> str:
    """Construct a full BeliefGraph node (Section 11.2's nine-key
    attribute dict) from a starting weight and add it to `graph`.

    Not itself given as a named code block anywhere in Section 11 --
    Section 11.2's `seed_node` only ever returns the two-key
    alpha/beta dict, not a full node (docs/DECISIONS.md item 45). This
    function is the "BeliefGraph construction" piece named alongside
    `seed_node`/`update_node`/`get_probability` in Section 3's tree
    comment and this week's kickoff checklist; it calls `seed_node`
    internally and fills in the remaining six attributes.

    `business_value` has no default: Section 11.2 gives an explicit
    fallback for `exploitability_score` ("from MentalModel if available;
    else 0.5") but no equivalent fallback for `business_value`, and
    `BusinessValue.UNKNOWN` is documented (core/ontology/enums.py's
    docstring, docs/DECISIONS.md item 27) as reserved solely for
    `deserialize_belief_graph`'s corrupted-checkpoint fallback -- using
    it here as a silent "not yet known" default would extend its meaning
    beyond that explicit scope. Callers must supply a real
    `BusinessValue` (computed by `info_gain_scorer.py`, Section 6.7)
    before a node is added.

    Args:
        graph: The `networkx.DiGraph` to add the node to (mutated
            in place).
        hypothesis_id: Unique identifier for this hypothesis; used as
            both the node's attribute value and its `networkx` node key.
        vuln_type: The vulnerability type this node represents.
        endpoint: The endpoint this hypothesis concerns.
        starting_weight: Passed to `seed_node` to compute initial
            alpha/beta.
        business_value: Required; see docstring above for why no
            default is given.
        exploitability_score: Defaults to 0.5 per Section 11.2's stated
            fallback ("from MentalModel if available; else 0.5").
        pinned_until: Defaults to `None` (not pinned). If provided, must
            be timezone-aware (module docstring, "DATETIME CONVENTION").
        now: Timestamp recorded as `last_updated`. Defaults to
            `datetime.now(timezone.utc)` if not supplied; overridable
            for deterministic tests.

    Returns:
        `hypothesis_id`, for convenience chaining.
    """
    seeded = seed_node(vuln_type, starting_weight)
    timestamp = now if now is not None else datetime.now(timezone.utc)
    graph.add_node(
        hypothesis_id,
        hypothesis_id=hypothesis_id,
        vuln_type=vuln_type,
        endpoint=endpoint,
        alpha=seeded["alpha"],
        beta=seeded["beta"],
        exploitability_score=exploitability_score,
        business_value=business_value,
        pinned_until=pinned_until,
        last_updated=timestamp,
    )
    return hypothesis_id


def update_node(graph: nx.DiGraph, node_id: str, success: bool) -> None:
    """Section 11.2, transcribed verbatim (alpha/beta int updates).

    Args:
        graph: The `networkx.DiGraph` containing `node_id`.
        node_id: The node to update.
        success: `True` increments `alpha` (signal observed); `False`
            increments `beta` (no signal). `last_updated` is refreshed
            to the current time either way.
    """
    graph.nodes[node_id]["alpha" if success else "beta"] += 1
    graph.nodes[node_id]["last_updated"] = datetime.now(timezone.utc)


def get_probability(graph: nx.DiGraph, node_id: str) -> float:
    """Section 11.2, transcribed verbatim: Beta-distribution posterior mean.

    Args:
        graph: The `networkx.DiGraph` containing `node_id`.
        node_id: The node to read.

    Returns:
        `alpha / (alpha + beta)`. Never divides by zero: `alpha + beta`
        starts at exactly 10 (`seed_node`'s two values always sum to 10)
        and only ever increases via `update_node`.
    """
    a = graph.nodes[node_id]["alpha"]
    b = graph.nodes[node_id]["beta"]
    return a / (a + b)


def should_prune(graph: nx.DiGraph, node_id: str, now: datetime) -> bool:
    """Evaluate Section 11.2's four pruning rules for a single node.

    PRECEDENCE (docs/DECISIONS.md item 47): the two "never prune" rules
    are checked first and short-circuit the two "prune" rules -- implied
    by the word "never" in Section 11.2's own bullet text
    ("`exploitability_score > 0.8` -> never prune", "`pinned_until >
    utcnow()` -> never prune (active chain)"), not an independent
    invention. A node that is both stale/low-probability AND pinned or
    highly exploitable is kept.

    TIME BASIS FOR RULE 1 (docs/DECISIONS.md item 47): Section 11.2's
    first rule reads "`get_probability(n) < 0.05` for 10+ min -> prune
    candidate" without naming which field supplies "10+ min," unlike
    rule 2, which explicitly names `last_updated`. Section 11.2's node
    schema has exactly one timestamp field (`last_updated`); no second
    field exists anywhere to track "how long has probability been below
    0.05" independently of when the node last changed. Reusing
    `last_updated` as the shared time basis for both time-based rules is
    the only reading that doesn't require inventing an unspecified
    tenth attribute onto Section 11.2's exhaustive nine-key node dict.

    Args:
        graph: The `networkx.DiGraph` containing `node_id`.
        node_id: The node to evaluate.
        now: The current time (timezone-aware; see module docstring).
            Required, not defaulted -- callers needing "now" to default
            should use `prune_graph`.

    Returns:
        `True` if the node should be pruned.
    """
    attrs = graph.nodes[node_id]

    pinned_until = attrs["pinned_until"]
    if pinned_until is not None and pinned_until > now:
        return False
    if attrs["exploitability_score"] > NEVER_PRUNE_EXPLOITABILITY_THRESHOLD:
        return False

    age_minutes = (now - attrs["last_updated"]).total_seconds() / 60.0
    probability = get_probability(graph, node_id)

    if probability < BELIEF_GRAPH_LIMITS["pruning_threshold"] and age_minutes >= PRUNE_LOW_PROBABILITY_STALE_MINUTES:
        return True
    if age_minutes > PRUNE_MODERATE_STALE_MINUTES and probability < PRUNE_MODERATE_PROBABILITY_THRESHOLD:
        return True
    return False


def prune_graph(graph: nx.DiGraph, now: datetime | None = None) -> list[str]:
    """Evaluate `should_prune` for every node in `graph` and remove matches.

    RULE EVALUATION ONLY, NOT A SCHEDULER (docs/DECISIONS.md item 47):
    Section 11.1's `pruning_interval` (300 seconds) is the cadence at
    which some future caller should invoke this function periodically;
    no session loop exists yet in this codebase to host that cadence
    (no `agent_self_monitor.py`-equivalent scoped to BeliefGraph pruning
    specifically) -- the same "framework built, calling infrastructure
    deferred" boundary already applied to `token_throttler.py`
    (docs/DECISIONS.md item 9) and `route_tier_d_action` (Week 2).
    `pruning_interval` is recorded in `BELIEF_GRAPH_LIMITS` for whenever
    that scheduler is built, but nothing in this module reads it.

    Args:
        graph: The `networkx.DiGraph` to prune, mutated in place.
        now: The current time (timezone-aware). Defaults to
            `datetime.now(timezone.utc)` if not supplied; overridable
            for deterministic tests.

    Returns:
        The list of `node_id`s that were removed, in no particular order.
    """
    current_time = now if now is not None else datetime.now(timezone.utc)
    to_remove = [node_id for node_id in graph.nodes if should_prune(graph, node_id, current_time)]
    graph.remove_nodes_from(to_remove)
    return to_remove


def serialize_belief_graph(g: nx.DiGraph) -> str:
    """Section 11.3, transcribed verbatim (NetworkX 3.x-safe serialization).

    `edges="links"` is mandatory for NetworkX 3.x compatibility on
    `node_link_data`, not optional styling (Section 11.3's own inline
    comment, and this week's kickoff instructions).

    Args:
        g: The `networkx.DiGraph` to serialize.

    Returns:
        A JSON string. `datetime` fields become ISO-8601 strings;
        `BusinessValue` becomes its `.value` string.
    """
    data = nx.node_link_data(g, edges="links")
    for node in data["nodes"]:
        for f in ("last_updated", "pinned_until"):
            if isinstance(node.get(f), datetime):
                node[f] = node[f].isoformat()
        if "pinned_until" in node and node["pinned_until"] is None:
            node["pinned_until"] = None
        if "business_value" in node and isinstance(node["business_value"], BusinessValue):
            node["business_value"] = node["business_value"].value
    return json.dumps(data)


def deserialize_belief_graph(raw: str) -> nx.DiGraph:
    """Section 11.3, transcribed verbatim, including the R-L4 fix.

    Corrupted `business_value` strings that don't match any
    `BusinessValue` member fall back to `BusinessValue.UNKNOWN` rather
    than raising and aborting the whole graph load; the ORIGINAL bad
    value is logged (captured before being overwritten, per Section
    11.3's `[v6.4-014]` comment), not the resulting `UNKNOWN`.

    Args:
        raw: A JSON string previously produced by `serialize_belief_graph`.

    Returns:
        The reconstructed `networkx.DiGraph`.
    """
    data = json.loads(raw)
    for node in data["nodes"]:
        for f in ("last_updated", "pinned_until"):
            if node.get(f):
                node[f] = datetime.fromisoformat(node[f])
        if "business_value" in node:
            raw_value = node["business_value"]  # [v6.4-014] captured BEFORE overwrite
            try:
                node["business_value"] = BusinessValue(raw_value)
            except ValueError:  # R-L4 fix: corrupted checkpoint safety
                node["business_value"] = BusinessValue.UNKNOWN
                logger.warning("[BELIEF_DESER] Unknown enum: %r", raw_value)
    return nx.node_link_graph(data, edges="links")
