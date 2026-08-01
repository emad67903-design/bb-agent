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

FIELD SPEC STATUS -- PARTIALLY PROVISIONAL (docs/DECISIONS.md item 29,
and item 38 for the `data_flows` addition below):
Section 3 gives no `@dataclass` field listing for `MentalModel` anywhere
(grep-confirmed across all ~13 occurrences of the name). The advisor
Claude's consolidated directive supplied a confirmed spec, split into
two tiers:

  CONFIRMED, cited directly to blueprint text (not provisional):
    - `business_purpose`, `roles`, `trust_boundaries`: Section 2.5's
      Context Window template (`{business_purpose}`, `{role_list}`,
      `{boundary_list}`).
    - `data_flows`: Section 6.3's Groq-call-1 description itself
      ("synthesise business purpose + roles + data flows") -- no
      Context Window placeholder names it the way `role_list`/
      `boundary_list` do for its siblings, which is exactly why this
      field was originally omitted and flagged rather than guessed at
      (item 29's first pass); added here once the citation was
      confirmed directly against Section 6.3's prose (item 38).
    - `assumptions` ({top_3_with_exploitability} -- a list of
      `Assumption`, each carrying the `exploitability_score` Section
      6.7's `info_gain_scorer.py` reads directly: "max(exploitability_score
      of relevant assumptions)").

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
  needing to block on them the way the first five fields' presence does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class RoleSignal:
    """One piece of page-level evidence for a user/actor role.
    mental_model_builder_prompt_design.md Section 2's response schema.

    Attributes:
        role: Short role name (e.g. "admin", "authenticated user").
        evidence: The exact string/tag the model found supporting it.
    """

    role: str
    evidence: str


@dataclass(frozen=True)
class FlowSignal:
    """One piece of page-level evidence for a data flow.
    mental_model_builder_prompt_design.md Section 2's response schema.

    Attributes:
        description: What moves, from where to where.
        evidence: The exact string, form action, or endpoint found.
    """

    description: str
    evidence: str


@dataclass(frozen=True)
class PageSignals:
    """One fetched page's local-7B extraction result -- produced by
    `core/mental_model/flow_tracer.py`, read by both it and
    `core/mental_model/role_mapper.py` (one call, two readers; see
    docs/DECISIONS.md item 39). Aggregated across up to 8 pages by
    `builder.py` before Groq call 1.

    Placed here (`core/ontology/`), not in `core/mental_model/`, for the
    same reason as `MentalModel`/`Assumption` (item 29): a dataclass
    consumed by more than one component belongs in the ontology, per
    the Engineering Constitution.

    Attributes:
        page_url: The page this extraction came from (`BrowserCapture.final_url`).
        role_signals: Role evidence found on this page. Empty list, not
            omitted, when none found (mental_model_builder_prompt_design.md
            Section 2: "never omit the key").
        flow_signals: Flow evidence found on this page. Same
            empty-list-not-omitted convention.
        injection_marker_detected: `True` if
            `core.mental_model._injection_guard.detect_injection_markers`
            flagged this page's raw content as containing instruction-
            like phrasing (docs/DECISIONS.md item 41). Detection only --
            does not mean this page's other signals were discarded or
            altered; a positive value is itself reportable evidence
            (e.g. a page attempting LLM-prompt injection is arguably a
            finding in its own right), not a reason to distrust the
            rest of this `PageSignals` instance.
    """

    page_url: str
    role_signals: list[RoleSignal] = field(default_factory=list)
    flow_signals: list[FlowSignal] = field(default_factory=list)
    injection_marker_detected: bool = False


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
        data_flows: Section 6.3's Groq-call-1 output, alongside
            `business_purpose` and `roles` (e.g. "user submits payment
            form -> processed by third-party gateway", "profile photo
            upload -> stored in S3"). No Context Window placeholder
            names this field the way `role_list`/`boundary_list` do for
            its siblings -- Section 6.3's own prose ("synthesise
            business purpose + roles + data flows") is the citation
            (item 38).
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
    data_flows: list[str] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    is_partial: bool = False
    pages_analyzed: int = 0
    built_at: datetime | None = None
