"""
Implements: Section 3 -- core/planning/hypothesis_engine.py (bare
filename, zero spec -- docs/DECISIONS.md item 64). Section 8.3's
"HYPOTHESIS_FALSE -> Generate alternative hypothesis" cascade line and
Section 11's BeliefGraph capacity/schema are this module's concrete
blueprint anchors.
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE, RATIFIED (docs/DECISIONS.md item 64's Section 39 architectural
review; items 67-68 close its two remaining NEEDS DECISION points): the
single chokepoint where a not-yet-committed hypothesis candidate is
validated, given an identity, deduplicated, capacity-checked, and
committed to the existing `BeliefGraph` (`belief_manager.py`, Week 4,
closed -- NOT modified by this file). Everything downstream of a
committed node (probability, pruning, serialization) remains entirely
`belief_manager.py`'s job, unchanged.

FUNCTIONS OVER A CLASS, DELIBERATELY -- same reasoning as
`hypothesis_tree.py` and `belief_manager.py` (docs/DECISIONS.md item
44): no state of its own; every function takes the caller's
`graph: nx.DiGraph` first.

HYPOTHESIS IDENTITY -- COARSE, TWO-TUPLE, NOT FOUR (docs/DECISIONS.md
item 67, superseding this review's own earlier Decision 1):
`hypothesis_id` is a deterministic function of `(vuln_type, endpoint)`
ONLY. `http_method`/`parameter` are Section 6.9's `DEDUP_KEY` fields --
that tuple is explicitly Finding-scoped (Section 6.9's own header,
"Phase 8: PoC Gate"; Section 14's rationale row is about not collapsing
two distinct REPORTS), a different concept at a later lifecycle stage.
The actual hypothesis-level evidence is Section 11.1's own capacity
arithmetic (`"max_nodes": 15_000, # ~500 endpoints x 29 vuln types +
padding` -- no method/parameter multiplier) and Section 11.2's exact
9-key node schema (no `http_method`, no `parameter` field) -- two
independent parts of the blueprint, mutually consistent, both silent
on method/parameter at the hypothesis-identity level specifically.
Confirmed by direct grep against the actual blueprint text before this
file was written, not transcribed on trust.

A CONSEQUENCE OF COARSE IDENTITY, WORTH STATING EXPLICITLY: two
structurally-different opportunities at the same (vuln_type, endpoint)
-- e.g. SQLi on `?id=` vs. SQLi on `?sort=` at the same path -- collapse
into ONE BeliefGraph node. This is not an oversight; it is the ONLY
identity granularity consistent with Section 11.1's own 500x29 sizing
(parameter-level identity would blow past `max_nodes=15_000` for any
endpoint with several parameters). It is also harmless downstream:
Section 6.9's Finding-level `DEDUP_KEY` -- untouched, still 4-tuple,
still parameter-aware -- is what actually governs report deduplication;
the BeliefGraph exists for coarse-grained Deep Lane prioritization
(Section 11's own stated purpose), not for tracking individual
parameters.

`http_method`/`parameter` ARE NOT STORED anywhere by this module -- not
as ID components, not as node attributes, not as optional extras. This
remains true after Week 7's `record_fast_lane_signal` (below,
docs/DECISIONS.md item 72): it is deliberately scoped to the identical
`(vuln_type, endpoint)` granularity as `seed_hypothesis`, not
`ExploitCandidate`'s finer 4-tuple (`ExploitCandidate` itself,
docs/DECISIONS.md item 69, resolves item 53's PROVISIONAL marking to
final -- `core/ontology/findings.py`, not this module).
`http_method`/`parameter` live on `ExploitCandidate`; this module's
identity granularity is unchanged by their existing elsewhere.

CAPACITY ENFORCEMENT -- LOCAL AND PARTIAL, NOT GLOBAL (docs/DECISIONS.md
item 68): `belief_manager.BELIEF_GRAPH_LIMITS["max_nodes"]`/
`["max_edges"]` are defined (Section 11.1) but were, as of this file's
introduction, enforced nowhere in the codebase -- grep-confirmed against
`core/cognitive/belief_manager.py` and its own test file before writing
this. `seed_hypothesis` below enforces `max_nodes` for the ONE path it
owns: before adding a genuinely new node, if the graph is already at
capacity, it calls the existing `prune_graph` (Week 4, unmodified) once,
re-checks, and raises `HypothesisGraphCapacityExceeded` only if still
full. This covers node-creation through this module only. Any future
Fast Lane -> BeliefGraph path (Week 7+) needs its own enforcement, or --
architecturally preferable -- this gets centralized inside
`add_belief_node` itself the next time `belief_manager.py` is
legitimately reopened. Not claimed as a global fix.

WEEK 7 RESOLUTION (docs/DECISIONS.md item 72): `record_fast_lane_signal`
is that Fast Lane -> BeliefGraph path, and it takes the non-"architecturally
preferable" branch this note flagged above -- it calls `seed_hypothesis`,
inheriting this module's existing capacity guard for free, rather than
centralizing enforcement inside `add_belief_node` itself. Confirmed
decision (week7_kickoff.md Phase 0 item 7's answer, adopted as-is): a
second guarded path into `add_belief_node` was explicitly rejected in
favor of joining the one that already exists. `add_belief_node` itself
remains exactly as unguarded as this paragraph originally described --
still true, not superseded.

`_injection_guard.py` IMPORT -- NAMED, EXPLICIT EXCEPTION (ratified):
this module is the seventh caller of `core/mental_model/_injection_guard.py`,
previously private to six `core/mental_model/` files (see that module's
own updated docstring). Only `detect_injection_markers` is used here,
for the same reason `flow_tracer.py` uses it -- DETECTION, NOT
BLOCKING: a positive match is logged (`[HYPOTHESIS_INJECTION_SUSPECTED]`),
never used to reject a candidate outright, matching the established
precedent exactly (module docstring of `_injection_guard.py`: "Callers
... decide what to do with a positive detection ... not silently
dropping the content"). `truncate_and_delimit` is deliberately NOT used
here: its stated purpose is bounding a string about to be embedded in
an LLM prompt, and nothing in this module builds an LLM prompt --
`seed_hypothesis`/`generate_alternative` are pure deterministic
validation-and-commit functions. Wiring it in anyway would be reaching
for a tool because it's available, not because there's a call site;
flagging this choice explicitly rather than silently deciding it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import networkx as nx

from core.cognitive.belief_manager import BELIEF_GRAPH_LIMITS, add_belief_node, prune_graph
from core.mental_model._injection_guard import detect_injection_markers
from core.ontology.enums import BusinessValue
from core.planning.hypothesis_tree import add_alternative_relationship

logger = logging.getLogger(__name__)


class HypothesisGraphCapacityExceeded(Exception):
    """Raised by `seed_hypothesis` when the graph is still at
    `BELIEF_GRAPH_LIMITS["max_nodes"]` after an opportunistic
    `prune_graph` call.

    Placement/naming precedent: `TotalChainBudgetExhausted` in
    `core/chain/chain_budget.py` -- defined locally in the module that
    enforces the limit, not a shared exceptions module (docs/DECISIONS.md
    item 68).
    """


def make_hypothesis_id(vuln_type: str, endpoint: str) -> str:
    """Deterministic hypothesis identity from `(vuln_type, endpoint)` only.

    See module docstring, "HYPOTHESIS IDENTITY -- COARSE, TWO-TUPLE, NOT
    FOUR" (docs/DECISIONS.md item 67) for the full grounding and
    rationale. No normalization (case, whitespace) is applied to either
    input -- neither the blueprint nor any existing caller of
    `add_belief_node` establishes a normalization convention for these
    strings (tests use bare, unnormalized values throughout), so none
    is invented here. `hypothesis_id` is treated as opaque by every
    other function in this module and in `hypothesis_tree.py` -- nothing
    ever parses it back into `vuln_type`/`endpoint`.

    Args:
        vuln_type: The vulnerability type (one of the 29 scanner-
            registry keys, e.g. "xss", "sqli" -- Section 3.1's scanner
            column convention).
        endpoint: The endpoint this hypothesis concerns.

    Returns:
        `f"{vuln_type}:{endpoint}"`. Same two inputs always produce the
        same ID; used as both the `hypothesis_id` node attribute and the
        `networkx` node key, matching `add_belief_node`'s existing
        contract.
    """
    return f"{vuln_type}:{endpoint}"


def _validate_candidate(
    vuln_type: str,
    endpoint: str,
    exploitability_score: float,
    starting_weight: float,
    business_value: BusinessValue,
) -> None:
    """Schema/semantic validation for a hypothesis candidate, before any
    graph mutation (module docstring's LLM-trust-boundary chokepoint).

    Raises:
        ValueError: If any field fails validation. Each failure states
            exactly which field and why, rather than a generic message.
    """
    if not isinstance(vuln_type, str) or not vuln_type.strip():
        raise ValueError(f"seed_hypothesis: vuln_type must be a non-empty string, got {vuln_type!r}")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError(f"seed_hypothesis: endpoint must be a non-empty string, got {endpoint!r}")
    if not isinstance(exploitability_score, (int, float)) or not 0.0 <= exploitability_score <= 1.0:
        raise ValueError(
            f"seed_hypothesis: exploitability_score must be in [0.0, 1.0], got {exploitability_score!r}"
        )
    if not isinstance(starting_weight, (int, float)) or not 0.0 <= starting_weight <= 1.0:
        # Not itself validated by belief_manager.seed_node (it trusts the
        # precondition per Section 11.2's own documented assumption) --
        # this is the first real caller to pass a potentially LLM/
        # MentalModel-sourced value, and an out-of-range w silently
        # produces a negative beta (e.g. w=1.5 -> alpha=15, beta=-5),
        # corrupting get_probability's a/(a+b) into a value outside
        # [0.0, 1.0]. Caught here rather than downstream.
        raise ValueError(f"seed_hypothesis: starting_weight must be in [0.0, 1.0], got {starting_weight!r}")
    if not isinstance(business_value, BusinessValue):
        raise ValueError(f"seed_hypothesis: business_value must be a BusinessValue member, got {business_value!r}")
    if business_value is BusinessValue.UNKNOWN:
        # BusinessValue.UNKNOWN is reserved exclusively for
        # deserialize_belief_graph's corrupted-checkpoint fallback
        # (core/ontology/enums.py's own docstring, docs/DECISIONS.md
        # item 27) -- info_gain_scorer.py's real scorer never produces
        # it "by design" (that module's own test name:
        # test_never_returns_unknown). A fresh candidate claiming
        # UNKNOWN is malformed input, not a legitimate score.
        raise ValueError("seed_hypothesis: business_value must not be BusinessValue.UNKNOWN for a new candidate")

    for field_name, value in (("vuln_type", vuln_type), ("endpoint", endpoint)):
        if detect_injection_markers(value):
            # DETECTION, NOT BLOCKING -- see module docstring,
            # "`_injection_guard.py` IMPORT". Logged, not rejected.
            logger.warning("[HYPOTHESIS_INJECTION_SUSPECTED] field=%s value=%r", field_name, value)


def seed_hypothesis(
    graph: nx.DiGraph,
    *,
    vuln_type: str,
    endpoint: str,
    starting_weight: float,
    business_value: BusinessValue,
    exploitability_score: float = 0.5,
    now: datetime | None = None,
) -> str:
    """Validates a hypothesis candidate, resolves its identity, and
    commits it to `graph` via `belief_manager.add_belief_node` -- the
    module's single LLM-candidate -> canonical-state chokepoint (module
    docstring; review Section 19).

    Idempotent on re-seed: if `make_hypothesis_id(vuln_type, endpoint)`
    already names a node in `graph`, that ID is returned immediately,
    with no capacity check and no mutation -- re-seeding an existing
    hypothesis is not graph growth.

    For a genuinely new node, enforces `BELIEF_GRAPH_LIMITS["max_nodes"]`
    (docs/DECISIONS.md item 68): if `graph.number_of_nodes()` is already
    at the cap, calls the existing `prune_graph` once (same `now`, for
    determinism) and re-checks before raising. A `prune_graph` call
    triggered here may remove OTHER, unrelated low-value nodes even on a
    call that ultimately still raises -- that is existing, desired,
    idempotent `belief_manager.py` behavior, not a side effect specific
    to this function, and does not itself constitute a partially-created
    hypothesis (see `generate_alternative`'s "both-or-neither" docstring
    for why this distinction matters).

    Args:
        graph: The `networkx.DiGraph` `BeliefGraph` to seed into.
        vuln_type: See `make_hypothesis_id`.
        endpoint: See `make_hypothesis_id`.
        starting_weight: Passed to `add_belief_node` -> `seed_node` to
            compute initial alpha/beta. Must be in [0.0, 1.0].
        business_value: Required; must not be `BusinessValue.UNKNOWN`
            (`_validate_candidate`).
        exploitability_score: Defaults to 0.5 per Section 11.2's stated
            fallback, matching `add_belief_node`'s own default.
        now: Timestamp used for `add_belief_node`'s `last_updated` AND,
            if a capacity-triggered prune is needed, for `prune_graph`'s
            `now`. Defaults to `datetime.now(timezone.utc)` if not
            supplied; overridable for deterministic tests.

    Returns:
        The hypothesis's `hypothesis_id` (existing or newly-created).

    Raises:
        ValueError: If the candidate fails `_validate_candidate`.
        HypothesisGraphCapacityExceeded: If the graph is still at
            `BELIEF_GRAPH_LIMITS["max_nodes"]` after one `prune_graph`
            attempt.
    """
    _validate_candidate(vuln_type, endpoint, exploitability_score, starting_weight, business_value)

    hypothesis_id = make_hypothesis_id(vuln_type, endpoint)
    if graph.has_node(hypothesis_id):
        return hypothesis_id

    timestamp = now if now is not None else datetime.now(timezone.utc)

    if graph.number_of_nodes() >= BELIEF_GRAPH_LIMITS["max_nodes"]:
        prune_graph(graph, now=timestamp)
        if graph.number_of_nodes() >= BELIEF_GRAPH_LIMITS["max_nodes"]:
            raise HypothesisGraphCapacityExceeded(
                f"seed_hypothesis: graph at {graph.number_of_nodes()}/"
                f"{BELIEF_GRAPH_LIMITS['max_nodes']} nodes after prune_graph; cannot seed {hypothesis_id!r}"
            )

    add_belief_node(
        graph,
        hypothesis_id=hypothesis_id,
        vuln_type=vuln_type,
        endpoint=endpoint,
        starting_weight=starting_weight,
        business_value=business_value,
        exploitability_score=exploitability_score,
        now=timestamp,
    )
    return hypothesis_id


def record_fast_lane_signal(
    graph: nx.DiGraph,
    *,
    vuln_type: str,
    endpoint: str,
    starting_weight: float,
    business_value: BusinessValue,
    exploitability_score: float = 0.5,
    now: datetime | None = None,
    tech_risk: float | None = None,
    dynamism: float | None = None,
) -> str:
    """Fast Lane's entry point into the BeliefGraph (Section 8.1:
    "FastLane -> BeliefGraph: ExploitCandidate list"; Section 6.6:
    "BeliefGraph updates: alpha += 1 on signal, beta += 1 on no-signal"
    -- the ALPHA/BETA UPDATE half of that sentence is `belief_manager.
    update_node`, already built, Week 4, unchanged, and out of scope for
    this function; this function is the SEEDING half, for a vuln_type/
    endpoint pair Fast Lane is observing for the first time this
    session).

    A thin wrapper over `seed_hypothesis`, not a new path into
    `add_belief_node` (docs/DECISIONS.md item 72, resolving
    week7_kickoff.md Phase 0 items 5 and 7 together): calling
    `seed_hypothesis` means this function inherits its capacity guard
    (`HypothesisGraphCapacityExceeded` on a still-full graph after one
    `prune_graph` attempt) for free -- see this module's own docstring,
    "WEEK 7 RESOLUTION," for why the alternative (centralizing
    enforcement inside `add_belief_node` itself) was flagged but not
    taken this week. `add_belief_node` gains no new caller and no new
    behavior from this addition.

    `starting_weight` IS THIS FUNCTION'S PARAMETER, NOT ITS LOOKUP: the
    caller (Fast Lane orchestration, not yet built) is expected to
    resolve it from `vuln_weights.yaml`'s per-vuln_type
    `starting_weights` (Section 9.5) before calling this function, the
    same way `seed_hypothesis`'s own callers already do. This function
    performs no file I/O and reads no YAML itself, matching the
    "ontology/planning types do no file I/O" precedent already
    established for `EvidenceChain.min_required`
    (`core/verifier/evidence_chain.py` resolves it from
    `vuln_thresholds.yaml`, not `EvidenceChain` itself).

    `tech_risk`/`dynamism` ARE TRUE NO-OPS THIS WEEK (week7_kickoff.md
    Phase 0 item 5's own explicit instruction, quoted: "design its
    signature to accept future optional fields ... even though you are
    NOT implementing what populates them this week ... Do not build the
    extension itself"): accepted here so a currently-separate,
    not-yet-approved extension doesn't need to reopen this function's
    signature later, then discarded -- not logged, not validated, not
    threaded into `seed_hypothesis` or `add_belief_node`, not stored
    anywhere. Neither parameter appears anywhere else in this function's
    body; this is deliberate, not an oversight.

    FLAT KWARGS, NOT A SINGLE `ExploitCandidate` PARAMETER (docs/
    DECISIONS.md item 72, per the build order's explicit instruction):
    `core.ontology.findings.ExploitCandidate` (item 69) exists as of
    this same bundle, but this function is not refactored to take one
    directly yet -- prove the flat version against Batch 1's real
    scanners first, then collapse to `ExploitCandidate` once it has
    actually been exercised, not before. Note the resulting overlap
    this creates deliberately, not by accident: an `ExploitCandidate`
    carries `http_method`/`parameter`/`detected_by`/etc. that this
    function's flat signature has no parameter for at all -- callers
    pass this function only the subset `seed_hypothesis` needs
    (`vuln_type`, `endpoint`, plus the BeliefGraph-specific
    `starting_weight`/`business_value`/`exploitability_score`), and are
    expected to separately construct and retain their own
    `ExploitCandidate` for whatever else consumes it (Verification,
    eventually) -- this function does not build, receive, or return one.

    Args:
        graph: The `networkx.DiGraph` `BeliefGraph` to seed into.
        vuln_type: See `make_hypothesis_id`.
        endpoint: See `make_hypothesis_id`.
        starting_weight: See `seed_hypothesis`. Caller-resolved; see
            above.
        business_value: See `seed_hypothesis`.
        exploitability_score: See `seed_hypothesis`.
        now: See `seed_hypothesis`.
        tech_risk: Unused this week. See above.
        dynamism: Unused this week. See above.

    Returns:
        The hypothesis's `hypothesis_id` (existing or newly-created) --
        identical return value and semantics to `seed_hypothesis`,
        since that is the entirety of this function's implementation.

    Raises:
        ValueError: See `seed_hypothesis`.
        HypothesisGraphCapacityExceeded: See `seed_hypothesis`.
    """
    return seed_hypothesis(
        graph,
        vuln_type=vuln_type,
        endpoint=endpoint,
        starting_weight=starting_weight,
        business_value=business_value,
        exploitability_score=exploitability_score,
        now=now,
    )


def generate_alternative(
    graph: nx.DiGraph,
    *,
    falsified_hypothesis_id: str,
    vuln_type: str,
    endpoint: str,
    starting_weight: float,
    business_value: BusinessValue,
    exploitability_score: float = 0.5,
    now: datetime | None = None,
) -> str:
    """Makes Section 8.3's `HYPOTHESIS_FALSE -> Generate alternative
    hypothesis` cascade line concrete: seeds a new hypothesis and links
    it to the falsified one via `hypothesis_tree.add_alternative_relationship`.

    "HYPOTHESIS_FALSE" ITSELF IS A CALLER-ASSERTED FACT, NOT SOMETHING
    THIS FUNCTION DECIDES (review Decision 5, ratified): no probability
    threshold is checked or hardcoded here. The blueprint names the
    trigger (Section 8.3) but never a number; the caller (today: a test
    or a human; eventually: whatever Deep Lane orchestration decides a
    hypothesis has been disproven) supplies `falsified_hypothesis_id` as
    a given.

    `vuln_type`/`endpoint` for the alternative are caller-supplied and
    expected to differ from the falsified hypothesis in at least one of
    the two dimensions -- same vuln_type at a different endpoint, or a
    different vuln_type at the same endpoint. Not enforced by this
    function (no blueprint citation requires it, and rejecting an
    identical resubmission would just make this call idempotent to the
    original hypothesis via `seed_hypothesis`'s own dedup, which is a
    reasonable outcome on its own, not an error).

    BOTH-OR-NEITHER ATOMICITY: `falsified_hypothesis_id`'s existence is
    checked FIRST, before `seed_hypothesis` runs -- so the only way this
    function creates a new node without also creating its relationship
    edge is if `add_alternative_relationship` itself is broken (it is
    not: both node IDs are guaranteed to exist by this point). If
    `seed_hypothesis` raises (bad candidate, or capacity exceeded), it
    does so before touching `graph`'s node set (see its own docstring),
    so no orphaned alternative is ever left behind either.

    Args:
        graph: The `networkx.DiGraph` `BeliefGraph`.
        falsified_hypothesis_id: The existing hypothesis being replaced.
            Must already be a node in `graph`.
        vuln_type: See `make_hypothesis_id`. For the NEW alternative.
        endpoint: See `make_hypothesis_id`. For the NEW alternative.
        starting_weight: See `seed_hypothesis`.
        business_value: See `seed_hypothesis`.
        exploitability_score: See `seed_hypothesis`.
        now: Shared timestamp for both the new node and the new edge.

    Returns:
        The new alternative hypothesis's `hypothesis_id`.

    Raises:
        KeyError: If `falsified_hypothesis_id` is not an existing node
            in `graph` -- checked before any mutation.
        ValueError: See `seed_hypothesis`.
        HypothesisGraphCapacityExceeded: See `seed_hypothesis`.
    """
    if falsified_hypothesis_id not in graph.nodes:
        raise KeyError(
            f"generate_alternative: falsified_hypothesis_id {falsified_hypothesis_id!r} "
            f"is not an existing node in graph"
        )

    alternative_id = seed_hypothesis(
        graph,
        vuln_type=vuln_type,
        endpoint=endpoint,
        starting_weight=starting_weight,
        business_value=business_value,
        exploitability_score=exploitability_score,
        now=now,
    )
    add_alternative_relationship(graph, alternative_id, falsified_hypothesis_id, now=now)
    return alternative_id
