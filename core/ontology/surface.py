"""
Implements: Section 3 -- core/ontology/surface.py's `EndpointSignals`
(one of four types Section 3 lists here: "EndpointSignals, SurfaceData,
AttackEdge, AttackGraph").
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPED TO `EndpointSignals` ONLY (docs/DECISIONS.md item 70, resolving
week7_kickoff.md's Phase 0 item 4). `SurfaceData`, `AttackEdge`, and
`AttackGraph` stay exactly as PROVISIONAL/deferred as
docs/DECISIONS.md items 10, 53, and 63 already left them -- not touched
by this file, this week, or this decision. Do not add them here without
a fresh decision; this module existing is not itself authorization to
fill in its other three named types.

WHY `EndpointSignals` AND NOT THE OTHER THREE: `Finding.reviewability.
passes_signal_gate`'s own Week 1 docstring (core/ontology/findings.py)
already says it is "set during Fast Lane from the EndpointSignals
check," and `core/verifier/deterministic_verifier.py`'s Week 1 header
repeats the identical forward reference. That dependency existed on
paper since Week 1 but had nothing real behind it, since no Fast Lane
scanner existed to exercise it -- Week 7's first real scanner batch is
what makes it real. `SurfaceData`/`AttackEdge`/`AttackGraph` have no
equivalent already-cited consumer: `core/chain/chain_engine.py` was
deliberately built against its own internal graph rather than
`AttackGraph` (docs/DECISIONS.md item 63), and nothing in Section
7.1-7.29's 29 workflows describes a scanner writing to any of the other
three -- only reading endpoints to test them.

NO FIELD-LEVEL SPEC EXISTS FOR `EndpointSignals` ANYWHERE IN THE
BLUEPRINT (grep-confirmed against all 16 sections) -- a strictly worse
starting position than `ExploitCandidate` had (item 53, at least backed
by Section 8.1's flow-diagram mention and Section 7.10's usage example).
Every field below is therefore an authored judgment call, not a
citation, kept deliberately minimal: exactly enough to make the one
real, already-cited consumer (`passes_signal_gate`) eventually
computable, nothing speculative beyond it. Flag if a richer shape is
wanted.

DELIBERATELY HOLDS NO `passes_signal_gate`-COMPUTING METHOD OR PROPERTY
OF ITS OWN: the real threshold this gate should key off already exists
and is already cited -- `core.verifier.evidence_chain.VulnThresholds.
min_signals_for(vuln_type)` (Section 5.4's `min_signals`, e.g. Section
7.1: "Signal min: 4" for XSS -- the Fast-Lane-level pre-filter, distinct
from and upstream of `min_evidence_types`'s Verification-layer gate).
But `VulnThresholds` lives in `core/verifier/`, and this is
`core/ontology/` -- giving `EndpointSignals` a method that compares
against a `VulnThresholds` instance would make an ontology type import
from the verifier layer, exactly the inverted dependency direction
`core/ontology/findings.py`'s own docstring already rules out for
`compute_triage_score` (docs/DECISIONS.md item 13: "verifier depends on
ontology, never the reverse"). The actual comparison
(`signal_counts.get(vuln_type, 0) >= thresholds.min_signals_for(
vuln_type)`) is therefore Fast Lane orchestration's job -- itself
unbuilt this week, same as the orchestration that would call
`core.planning.hypothesis_engine.record_fast_lane_signal`
(docs/DECISIONS.md item 72) -- not this dataclass's.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EndpointSignals:
    """Per-(endpoint, vuln_type) raw Fast Lane detection counts. See
    this module's docstring for why this type exists, why it is scoped
    to exactly these two fields, and why it deliberately has no
    threshold-comparing method of its own.

    Attributes:
        endpoint: The endpoint this aggregation concerns (same
            convention as `ExploitCandidate.endpoint` /
            `Finding.endpoint`).
        signal_counts: `vuln_type -> raw Fast Lane hit count at this
            endpoint` (e.g. the number of distinct
            `ExploitCandidate`s a given vuln_type's scanner has raised
            here so far). A properly-typed counter mapping (`str` keys,
            `int` values, exactly matching `core.verifier.
            evidence_chain.VulnThresholds.min_signals`'s own
            `dict[str, int]` shape), not a `dict[str, Any]` catch-all --
            the loosely-typed escape hatch the ontology-first rule
            forbids, and the same design docs/DECISIONS.md item 69's
            `ExploitCandidate` discussion considered and rejected for
            `payload_used`/etc. (a `raw_signal: dict[str, Any]` option
            there).
    """

    endpoint: str
    signal_counts: dict[str, int] = field(default_factory=dict)
