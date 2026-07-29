"""
Implements: Section 3 -- core/ontology/enums.py (PayloadFileType, EvidenceType)
Also implements: Section 10.1 / Section 3's safety_gate.py comment
(TierLevel -- see WEEK 2 note below).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 2 ADDITION -- TierLevel (AUTHORIZED, not inferred):
`TierLevel` is used with ordinal comparison in two places (Section 3's
safety_gate.py tree comment: "max_allowed_tier = TierLevel.TIER_B";
Section 10.1's code block: "if requested_tier > TierLevel.TIER_B"), but
is absent from the eleven enums Section 3 explicitly names for this
file (grep-verified: neither occurrence is in that list). This is a
foundational ontology type referenced by, and needed by, TWO Week 2
components at once (core/governance/autonomous_risk_gate.py and
core/governance/safety_gate.py) -- the Engineering Constitution's STOP
CONDITIONS apply to exactly this shape of gap (a type consumed by more
than one component, not yet declared anywhere), not the narrower
"document a judgment call and proceed" pattern used elsewhere in this
file for single-consumer gaps. Flagged as a hard stop mid-session;
member set, ordering, and values below were then explicitly authorized
by the user rather than inferred -- see docs/DECISIONS.md, Week 2
section, for the full exchange. `IntEnum` (not the `(str, Enum)` mixin
PayloadFileType/EvidenceType use) is required because Section 10.1's own
code performs ordinal comparison (`>`) between members; a str-mixin
would compare lexicographically on whatever string value each member
carried, which -- for the tier's own descriptive slugs
(read_only/low_risk_probe/state_changing/destructive) -- does NOT sort
in risk order and would make `>` silently wrong.

WEEK 0/1 SCOPE NOTE: Section 3 names eleven enums for this module
(FailureCause, RiskLevel, WorkflowConfidence, HumanFeedback,
MemoryEntryStatus, CalibrationBand, EvidenceType, PayloadFileType,
TargetType, BusinessValue, BudgetProfile). PayloadFileType (Week 0) and
EvidenceType (Week 1 -- consumed by core/ontology/findings.py's
EvidenceChain and core/verifier/evidence_chain.py, per docs/DECISIONS.md
item 4) are implemented. TargetType and BusinessValue are added this
week (Week 3, see below). The remaining seven are intentionally NOT
built yet -- per the Engineering Constitution's strict week ordering,
building them now would be pulling later weeks' contracts forward
before the sections that define their exact members and usage are
actually implemented. They will be added to THIS file (never a
parallel file) as their owning week arrives -- ontology-first, single
source of truth. Per-enum landing point (docs/DECISIONS.md item 4,
checked directly against Section 12 rather than assumed):
  - BudgetProfile -> no week explicitly names this in Section 12. Lands
    whenever config.py/AgentConfig is first substantively built, or no
    later than Week 7 if that comes first (docs/DECISIONS.md item 4).
    [WEEK 1 FIX: this comment previously said "BudgetProfile -> Week 3",
    asserted before docs/DECISIONS.md's own correction commit (6a844f5,
    "fix unverified BudgetProfile->Week3 assertion -- Section 12 names no
    week for it") was written. That commit fixed DECISIONS.md but never
    propagated here -- exactly the "incomplete propagation" failure mode
    this project has hit before in blueprint revisions. Fixed now.]
  - FailureCause, RiskLevel, WorkflowConfidence, HumanFeedback,
    MemoryEntryStatus, CalibrationBand -> not yet mapped to a specific
    week; will be added when their first real consumer is implemented.

WEEK 3 ADDITION -- TargetType (verbatim, no ambiguity):
Section 3 names this enum directly, with all 7 members and their
string values given explicitly (SPA, REST_API, MVC_WEB, GRAPHQL,
ECOMMERCE, ADMIN_PANEL, UNKNOWN). Its only Week 3 consumer is
`TargetAdapter` (core/planning/target_adapter.py, used by
CampaignPlanner, Section 6.4) -- single consumer, no cross-component
ambiguity of the kind that triggered TierLevel's hard stop (item 20).
`(str, Enum)` mixin, matching PayloadFileType/EvidenceType: no ordinal
comparison is used on this type anywhere in the blueprint.

WEEK 3 ADDITION -- BusinessValue (4 members, not 3 -- pre-flagged and
now resolved, see docs/DECISIONS.md item 27 for the full record):
Section 3's own listing here shows only 3 members (LOW/MEDIUM/HIGH).
Section 11.3's BeliefGraph deserialization code
(`except ValueError: node["business_value"] = BusinessValue.UNKNOWN`)
requires a 4th member that Section 3 never lists. This exact gap was
already caught and logged at Week 0 close (docs/DECISIONS.md item 4's
"Flag for whenever BusinessValue is actually implemented" note, itself
written before this file had EITHER TargetType or BusinessValue) --
this is that flag's resolution, not a fresh guess: `UNKNOWN` is added
as a 4th member now so Week 4's own R-L4 fix type-checks when it
arrives, rather than this file re-creating the exact gap Week 4 would
otherwise inherit. `(str, Enum)` mixin -- no ordinal comparison is used
on this type anywhere in the blueprint (Section 6.7's trigger condition
and Section 8.1's BeliefGraph->DeepLane flow both use `==`, never `<`/`>`);
this also matches this module's own PayloadFileType docstring, written
back in Week 0, which already assumed "the BusinessValue enum's
serialization treatment in Section 11.3" would be a str-mixin.

Scope boundary, stated explicitly: this enum's own 4th member,
`UNKNOWN`, is reserved EXCLUSIVELY for Week 4's BeliefGraph
deserialization fallback path (R-L4). `info_gain_scorer.py`'s actual
scoring function (Section 6.7: HIGH if max(exploitability_score) >= 0.7,
MEDIUM if 0.4 <= max < 0.7, LOW if max < 0.4), built this session in
`core/planning/info_gain_scorer.py`, never returns `BusinessValue.UNKNOWN`
-- there is no score threshold that produces it, by design (see
`test_never_returns_unknown` in that module's own test file).

`UNKNOWN`'s string value ("unknown") is a documented placement choice,
not a blueprint citation -- Section 11.3 names the member but never
gives it a literal string value anywhere (it's constructed directly as
`BusinessValue.UNKNOWN`, never from a string). Chosen to match
TargetType's own `UNKNOWN = "unknown"` convention two members above,
for consistency within this same file.
"""

from __future__ import annotations

from enum import Enum, IntEnum


class PayloadFileType(str, Enum):
    """Classification for files under data/payloads/ (Section 3.1).

    INJECTABLE_PAYLOAD files are consumed by payload_engine.py for HTTP
    injection. PATTERN_LIBRARY files are consumed directly by their
    scanner for content matching and are never routed through
    payload_engine.py (Section 3.1). The str mixin makes instances
    serialize as their plain string value (consistent with the
    BusinessValue enum's serialization treatment in Section 11.3).
    """

    INJECTABLE_PAYLOAD = "injectable_payload"
    PATTERN_LIBRARY = "pattern_library"


class EvidenceType(str, Enum):
    """The 7 core evidence types plus the 7 named substitutes (Section 5.1/5.2).

    Core types (Section 5.1) are what AutonomousTriage / the safe-exploit
    harnesses collect directly. Substitute types (Section 5.2) exist
    because some vuln classes cannot achieve every core type (e.g.
    boolean-blind SQLi has no OOB channel) -- a substitute stands in for
    whichever core type(s) map to it in `evidence_substitutes`
    (configs/vuln_thresholds.yaml, Section 5.4).

    COUNTING RULE (Section 5.2, enforced by EvidenceChain.meets_per_vuln_minimum
    in core/ontology/findings.py): a unique member of THIS enum collected
    = exactly 1 evidence slot, whether it's a core type or a substitute,
    and regardless of how many core types map to the same substitute in
    the YAML (e.g. collecting `boolean_differential_confirmed` once is 1
    slot, even though it substitutes for BOTH `cross_scanner` and
    `chain_proven` for SQLi -- Section 5.2's SQLi rows).

    The str mixin matches PayloadFileType's serialization convention.
    """

    # --- 7 core types (Section 5.1) ---
    REPLAY_STABLE = "replay_stable"
    OOB_INTERACTION = "oob_interaction"
    DIFFERENTIAL = "differential"
    TIMING_ANOMALY = "timing_anomaly"
    CROSS_SCANNER = "cross_scanner"
    VARIANT_CONFIRMED = "variant_confirmed"
    CHAIN_PROVEN = "chain_proven"

    # --- 7 named substitutes (Section 5.2) ---
    DOM_EXECUTION_CONFIRMED = "dom_execution_confirmed"              # XSS
    INJECTION_CONFIRMED = "injection_confirmed"                      # SQLi
    BOOLEAN_DIFFERENTIAL_CONFIRMED = "boolean_differential_confirmed"  # SQLi (v6.5 redesign)
    CROSS_SITE_EXECUTION_CONFIRMED = "cross_site_execution_confirmed"  # CSRF
    WORKFLOW_TRACE_CONFIRMED = "workflow_trace_confirmed"            # Business Logic
    CROSS_ACCOUNT_READBACK = "cross_account_readback"                # IDOR, BAC
    TIMING_CONFIRMED = "timing_confirmed"                            # Race


class TierLevel(IntEnum):
    """Section 10.1's four Permanent Tier Boundaries. AUTHORIZED addition
    (Week 2), not one of Section 3's eleven named enums -- see this
    module's header docstring and docs/DECISIONS.md for why this is
    documented as an authorized decision rather than an inference.

    Ordinal by construction: TIER_A < TIER_B < TIER_C < TIER_D, matching
    Section 10.1's own table order (least to most risky) and enabling
    the exact comparison Section 10.1's code block performs verbatim:
    `if requested_tier > TierLevel.TIER_B`.

    Members (value = ordinal rank, not a serialized slug; the
    parenthetical class name from Section 10.1's table is preserved
    below only as documentation):
        TIER_A = 1  -- read_only.        Always autonomous.
        TIER_B = 2  -- low_risk_probe.    Always autonomous. VDP caps here.
        TIER_C = 3  -- state_changing.    Autonomous only if reversible,
                                          test accounts, safe exploit proven.
                                          VDP: not permitted.
        TIER_D = 4  -- destructive.       NEVER autonomous. Human approval
                                          required. Permanent, no exceptions
                                          (Section 10.1; restated as a core
                                          invariant at the project level).
    """

    TIER_A = 1  # read_only
    TIER_B = 2  # low_risk_probe
    TIER_C = 3  # state_changing
    TIER_D = 4  # destructive


class TargetType(str, Enum):
    """Target application classification (Section 3, 7 members, verbatim).

    Backs `TargetAdapter` (core/planning/target_adapter.py), consumed by
    `CampaignPlanner` (Section 6.4) to select an ordered test plan and
    scanner weights appropriate to the target's shape. Single Week 3
    consumer path -- no cross-component ambiguity of the kind that
    required a hard stop for TierLevel (item 20) or MentalModel
    (docs/DECISIONS.md item 29).
    """

    SPA = "spa"
    REST_API = "rest_api"
    MVC_WEB = "mvc_web"
    GRAPHQL = "graphql"
    ECOMMERCE = "ecommerce"
    ADMIN_PANEL = "admin_panel"
    UNKNOWN = "unknown"


class BusinessValue(str, Enum):
    """Exploitability-derived value tier for a BeliefGraph hypothesis
    (Section 3 + Section 11.3; 4 members -- see this module's docstring
    and docs/DECISIONS.md item 27 for why 4, not Section 3's literal 3).

    Computed by `info_gain_scorer.py` from a MentalModel's assumptions:
    HIGH if max(exploitability_score of relevant assumptions) >= 0.7,
    MEDIUM if 0.4 <= max < 0.7, LOW if max < 0.4 (Section 6.7). Stored on
    BeliefGraph nodes (Section 11.2) and used, by equality only (never
    ordinally), as part of Deep Lane's activation trigger (Section 6.7:
    `business_value == BusinessValue.HIGH`).

    `UNKNOWN` is NOT one of the three scoring outcomes above. It exists
    solely as Week 4's BeliefGraph-deserialization fallback (Section
    11.3, R-L4 fix) when a corrupted checkpoint's stored value fails to
    parse as one of LOW/MEDIUM/HIGH. `info_gain_scorer.py`'s scoring
    function must never produce it.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"
