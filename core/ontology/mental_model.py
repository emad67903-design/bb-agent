"""
Implements: Section 3 -- core/ontology/state.py's "MentalModel field"
(the type itself, per the ontology-placement resolution below) and
core/mental_model/model.py ("MentalModel itself").
Also implements: Section 2.5 (Context Window's "MENTAL MODEL (Summary)"
template), Section 6.3 (Phase 2 output: "MentalModel in
ParentState.mental_model").
Blueprint: bb_agent_v6.6_final_blueprint.md

PLACEMENT (docs/DECISIONS.md item 29, resolved by the project owner):
`core/ontology/`, not `core/mental_model/model.py`, per the
`JSAnalysisResult` precedent -- a JS/recon-parsing type that nonetheless
gets a full `@dataclass` field listing in `core/ontology/surface.py`,
not a recon-specific directory. This new, narrowly-scoped file is used
instead of adding to `surface.py` itself, since that file does not exist
yet in this repository and its real eventual scope (`EndpointSignals`,
`SurfaceData`, `AttackEdge`, `AttackGraph`, `ExploitCandidate` fields,
`JSAnalysisResult`) belongs to later weeks.
`core/mental_model/model.py` is a thin re-export of `MentalModel` from
here, preserving Section 3's file tree without duplicating the type.

FIELD SPEC STATUS -- PARTIALLY PROVISIONAL (docs/DECISIONS.md item 29):
Section 3 gives no `@dataclass` field listing for `MentalModel` anywhere
(grep-confirmed across all ~13 occurrences of the name). The advisor
Claude's consolidated directive supplied a confirmed spec, split into
two tiers:

  CONFIRMED, cited directly to Section 2.5's Context Window template
  (not provisional): `business_purpose` ({business_purpose}), `roles`
  ({role_list}), `trust_boundaries` ({boundary_list}), `assumptions`
  ({top_3_with_exploitability} -- a list of `Assumption`, each carrying
  the `exploitability_score` Section 6.7's `info_gain_scorer.py` reads
  directly: "max(exploitability_score of relevant assumptions)").

  RECOMMENDED, not blueprint-cited -- PROVISIONAL, flagged explicitly
  rather than silently treated as equally authoritative:
    - `is_partial`: supports Section 6.3's actual specified behavior
      ("abort -> use partial model + log [MENTAL_MODEL_PARTIAL]",
      docs/DECISIONS.md item 15) with a real field a caller can branch
      on, rather than requiring downstream code to re-parse log output
      to learn the same fact.
    - `pages_analyzed`: gives `is_partial` a concrete number behind it
      (how far the 8-page cap got before an abort), not itself named in
      the blueprint.
    - `built_at`: supports Section 1.4's "revisable during session"
      principle for `MentalModel` (a caller can tell how stale a given
      instance is) -- not itself named in the blueprint.
  None of these three participate in any blueprint-given formula
  (Section 6.7's scoring reads only `assumptions[*].exploitability_score`)
  -- their absence or renaming would not break any cited computation,
  which is exactly why they're safe to carry as provisional rather than
  needing to block on them the way the first four fields' presence does.

`data flows` (Section 6.3's Groq-call-1 description, alongside business
purpose and roles) has NO corresponding field here -- no Context Window
placeholder hints at its shape the way `role_list`/`boundary_list` do
for the other two, so it is omitted rather than guessed at. Flagged,
not silently dropped: if a future week's actual Groq-calling
implementation needs to carry this, it belongs here, added to this same
file, not invented as an untyped dict elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Assumption:
    """One developer assumption extracted from the target application,
    scored for how exploitable violating it would be.

    Section 6.3: "(3) extract developer assumptions" (Groq call),
    "(4) score each assumption's exploitability_score (0.0-1.0)" (Groq
    call). Section 6.7's `info_gain_scorer.py` reads
    `exploitability_score` directly off instances of this type: "HIGH if
    max(exploitability_score of relevant assumptions) >= 0.7."

    Attributes:
        description: The assumption itself, in prose (e.g. "session
            cookies are httponly and not readable from JS"). Confirmed
            field name per the advisor Claude's consolidated directive;
            this session's own independent proposal used `text` for the
            same field before that confirmation.
        exploitability_score: 0.0-1.0. The sole input to
            `info_gain_scorer.py`'s `BusinessValue` computation
            (Section 6.7) -- the one field on this type any committed,
            cited formula actually depends on.
    """

    description: str
    exploitability_score: float


@dataclass(frozen=True)
class MentalModel:
    """Section 6.3's Phase 2 output; Section 2.5's Context Window
    "MENTAL MODEL (Summary)" block is a display projection of this.
    Stored as `ParentState.mental_model` (Section 3's `state.py` tree
    comment, Section 6.3's "Output" line) -- `ParentState` itself does
    not exist yet in this repository (confirmed by directory listing);
    that is a separate, later scope question, not resolved here.

    Attributes:
        business_purpose: Context Window `{business_purpose}` (Section
            2.5). What the target application is for, in prose.
        roles: Context Window `{role_list}` (Section 2.5). The distinct
            user/actor roles `MentalModelBuilder` identified (e.g.
            "anonymous visitor", "authenticated customer", "admin").
        trust_boundaries: Context Window `{boundary_list}` (Section
            2.5). Points in the application where trust level changes
            (e.g. "unauthenticated -> authenticated", "customer ->
            admin").
        assumptions: Context Window `{top_3_with_exploitability}`
            (Section 2.5 shows only the top 3 for display purposes;
            this field holds the full set `MentalModelBuilder`
            extracted -- truncation to a top-N is a Context Window
            rendering concern, Section 2.5's own truncation-priority
            list, not a storage concern for this type).
        is_partial: PROVISIONAL (not blueprint-cited as a field, though
            the behavior it represents is: Section 6.3, "abort -> use
            partial model + log [MENTAL_MODEL_PARTIAL]"). `True` when
            the 8-page/90-second cap (Section 6.3) forced an early stop.
        pages_analyzed: PROVISIONAL. How many of the (up to 8) selected
            pages were actually analyzed before completion or abort.
        built_at: PROVISIONAL. When this instance was produced --
            supports Section 1.4's "revisable during session" principle
            for `MentalModel` by letting a caller compare freshness.
    """

    business_purpose: str
    roles: list[str] = field(default_factory=list)
    trust_boundaries: list[str] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    is_partial: bool = False
    pages_analyzed: int = 0
    built_at: datetime | None = None
