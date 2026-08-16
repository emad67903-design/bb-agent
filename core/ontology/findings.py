"""
Implements: Section 3 -- core/ontology/findings.py
  ("Finding, Evidence, ExploitCandidate, PoC, EvidenceChain, TriageResult";
  "Reviewability (v6.3)"; "Finding.reviewability: Reviewability";
  "Finding.evidence.verified: bool"; "SUBSTITUTE COUNTING RULE")
Also implements: Section 6.8 (AutonomousTriage 9-state scoring,
EvidenceChain.strength formula), Section 6.9 (PoC Gate / is_reportable).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 1 SCOPE (Section 12 Week 1 row: "Reviewability @dataclass,
evidence.verified completeness flag, ... EvidenceChain.strength formula,
substitute counting rule (1 slot)"):

  Built now:      TriageResult, compute_triage_score, Reviewability,
                  EvidenceChain, Evidence, Finding (incl. is_reportable).
  Deferred:       ExploitCandidate, PoC. Section 3 names both as living in
                  this file but gives no field-level spec for either
                  anywhere in the blueprint (confirmed by grep -- every
                  other reference to them is a flow-diagram mention, e.g.
                  "FastLane -> BeliefGraph: ExploitCandidate list", Section
                  8.1, or "LLM-generated Python PoC", Section 10.7 -- never
                  a field list). Neither is required by any of Week 1's
                  named deliverables. Building them now with invented
                  fields would be exactly the "silent invention" the
                  Engineering Constitution's STOP CONDITIONS forbid.
                  Deferred the same way Week 0 deferred 10 of 11 enums:
                  added to THIS file, never a parallel one, when their
                  first real consumer (BeliefGraph for ExploitCandidate --
                  Week 4/5; sandbox/poc_generator.py for PoC -- Week 1's
                  own poc_generator.py stub or Week 6) is actually built.
                  See docs/DECISIONS.md Week 1 section.

WEEK 7 UPDATE (docs/DECISIONS.md item 69): `ExploitCandidate` built.
File ownership (item 53's PROVISIONAL placement, open since item 10)
confirmed here as final -- see the class's own docstring for the
project owner's reasoning and the still-unrebutted counter-argument,
logged rather than silently dropped. `PoC` remains deferred; nothing in
this week's scope constructs one (item 10's own reasoning for `PoC`
still holds unchanged).

`compute_triage_score` lives here, not in core/verifier/autonomous_triage.py,
because it is pure arithmetic over TriageResult -- the enum defined in
this same file -- with no I/O and no scanner-specific behavior. Putting it
in core/verifier/ would make this ontology module import FROM the
verifier layer to implement EvidenceChain.triage_score, inverting the
intended dependency direction (verifier depends on ontology, never the
reverse). core/verifier/autonomous_triage.py itself -- the component that
actually RUNS the L1 replay / L2 variant / L3 cross-scanner checks against
live scanner output to PRODUCE TriageResult values in the first place --
is Week 7 scope (explicitly named in the Week 7 row: "AutonomousTriage
9-state matrix"), since it needs the 29 scanners to exist. See
docs/DECISIONS.md Week 1 section for the full reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from core.ontology.enums import EvidenceType

# Section 6.8: "strength ∈ [0.0, 1.0]"; ≥0.71 auto-report, ≥0.43 report
# with notes, ≥0.29 deep verify, else [LOW_CONFIDENCE] (renamed from
# "not reportable" in v6.4->v6.5's V6.4-L2 fix, since that label collided
# with the is_reportable gate term). These are advisory thresholds on the
# REPORT-QUALITY signal, not a second gate -- is_reportable is computed
# independently (see Finding.is_reportable below) and is sole authority
# (Section 6.9 "Conflict resolution").
_STRENGTH_AUTO_REPORT_THRESHOLD = 0.71
_STRENGTH_REPORT_WITH_NOTES_THRESHOLD = 0.43
_STRENGTH_DEEP_VERIFY_THRESHOLD = 0.29

# Section 6.8: "evidence_ratio = min(len(unique_collected_types), 7) / 7.0"
# -- the min(...,7) cap is the v6.4-008 fix; without it a finding collecting
# all 7 core types PLUS an independently-firing substitute could yield
# len(unique_collected_types)=8, pushing strength above the documented
# [0.0, 1.0] bound.
_MAX_EVIDENCE_TYPES_FOR_RATIO = 7

# Section 6.8: "triage_ratio = triage_score / 3.0  # triage_score ∈ {0, 1, 3}"
_MAX_TRIAGE_SCORE = 3.0

# Section 6.8: "strength = round(0.7 * evidence_ratio + 0.3 * triage_ratio, 3)"
_EVIDENCE_RATIO_WEIGHT = 0.7
_TRIAGE_RATIO_WEIGHT = 0.3


@dataclass
class ExploitCandidate:
    """Fast Lane's raw scanner output (Section 8.1: "FastLane ->
    BeliefGraph: ExploitCandidate list"; Section 7.10's usage,
    "lfi_scanner.py... returns 0 ExploitCandidates"). One instance per
    positive detection event, upstream of and structurally simpler than
    `Finding` -- nothing here has been triaged, evidence-collected, or
    verified yet; that is what the rest of this file's pipeline
    (`Evidence`, `EvidenceChain`, `Finding`) does to a candidate that
    survives long enough to matter.

    FILE OWNERSHIP -- CONFIRMED, NOT JUST PROVISIONAL (docs/DECISIONS.md
    item 69, resolving item 53): lives here, in `findings.py`, not
    `core/ontology/surface.py`. Reasoning beyond item 53's own textual
    argument (4 of `ExploitCandidate`'s 5 findings.py list-mates already
    confirmed to live here): `ExploitCandidate` reads into `Finding`
    downstream, so one file avoids needless cross-file coupling between
    two types in the same maturation pipeline; and shape-wise, this
    class is a per-vulnerability-instance record, closer to `Finding`
    (also per-instance) than to `EndpointSignals` (a per-endpoint
    aggregate, core/ontology/surface.py, item 70). The Fast-Lane-thematic
    counter-argument item 53 logged -- that raw scanner output belongs
    with `surface.py`'s other raw-reconnaissance types -- is REAL and
    UNREBUTTED; the call is made anyway, for the reasons above. Recorded
    here, not silently dropped, per the same discipline item 53 itself
    modeled.

    FIELDS -- four provenances, each documented at its own attribute
    below: (1) directly blueprint-cited, (2) authored to make an
    already-cited downstream consumer computable, (3) authored and
    scoped to Batch 1 only (docs/DECISIONS.md item 69's own build
    order), pending confirmation against Batches 2-7's actual scanners.

    Attributes:
        vuln_type: One of the 29 scanner-registry keys. Same convention
            as `Finding.vuln_type` / `EvidenceChain.vuln_type` (plain
            `str`, not a new enum -- same reasoning as both: no
            "VulnType" enum exists, `SCANNER_REGISTRY` (Week 5) is the
            real source of truth once populated).
        endpoint: The endpoint this candidate concerns. Same convention
            as `Finding.endpoint` (Section 8.1's flow, matches
            `make_hypothesis_id`'s own parameter name).
        http_method: Section 6.9's `DEDUP_KEY = (vuln_type,
            endpoint_path, http_method, parameter)` -- the field
            week7_kickoff.md names as deferred to this week. GET vs.
            POST SQLi on the same endpoint are different findings
            (Section 6.9); a candidate must carry this from the moment
            a scanner raises it, not have it reconstructed later.
        parameter: Same `DEDUP_KEY` citation. Required, not defaulted
            (see class docstring above on why `parameter`, unlike
            `payload_used`/`raw_response_snapshot`/`probe_correlation_id`
            below, forces an explicit choice) -- but nullable: CORS
            (Origin header, not a query/body parameter), Host Header
            (Host header), CSRF (whole-form action), and Auth
            (state-machine test name, e.g. "session_fixation") all lack
            a single query/body parameter in the conventional sense.
            Confirmed as the four no-single-parameter cases
            (docs/DECISIONS.md item 69); every other vuln_type is
            expected to supply a real value.
        detected_by: The `SCANNER_REGISTRY` key of the scanner that
            raised this candidate (Section 4.4's `caller_id` convention,
            e.g. `"xss_scanner"`). AUTHORED, not cited verbatim anywhere
            as an `ExploitCandidate` field -- added because Section
            7.29's `cross_scanner` definition for Nuclei ("a NON-Nuclei
            scanner also flagged the same endpoint") is uncomputable
            later without knowing which scanner raised which candidate,
            and the same need generalizes to every other vuln_type's own
            `cross_scanner` evidence type (Section 5.1).
        detected_at: When this candidate was raised. AUTHORED -- needed
            for Section 11.2's BeliefGraph pruning windows
            (`last_updated > 60 min AND probability < 0.15 -> prune`)
            once this candidate becomes a hypothesis node, and general
            session chronology (Section 6.1). Defaults to the
            construction-time UTC timestamp (same "sensible default,
            overridable for deterministic tests" pattern already used by
            `belief_manager.add_belief_node`'s and
            `hypothesis_engine.seed_hypothesis`'s own `now` parameters)
            rather than requiring every one of the 29 scanners to pass
            it explicitly.
        payload_used: The exact payload or probe value that produced
            this signal (e.g. an XSS reflection string, a SQLi UNION
            probe). AUTHORED, Batch 1 only (docs/DECISIONS.md item 69):
            confirmed sufficient for xss/sqli/ssti/lfi/path_traversal
            (Batch 1), none of which need count-based evidence. NOT yet
            confirmed sufficient for Batches 2-7 -- Race's
            `success_count`/`total` (Section 7.5) is already flagged as
            a concrete case none of this class's three Batch-1 fields
            can express; the first later-batch scanner that hits this
            gap stops and flags it back rather than silently reaching
            for a new field or a dict (docs/DECISIONS.md item 69,
            per the Engineering Constitution's ontology-first rule).
        raw_response_snapshot: Truncated response body backing the
            signal, same 512-character convention as `call_target()`'s
            `body_preview` (Section 10.7) and `race.go`'s `BodyPreview`
            -- the value stored here is expected to already be
            truncated by the caller (this class does no truncation or
            other validation itself, matching this file's existing thin-
            dataclass convention throughout: `EvidenceChain`, `Evidence`,
            and `Finding` above also perform no `__post_init__`
            validation on their own fields).
        probe_correlation_id: The interactsh correlation ID (Section
            4.2: `correlation_id = f"XBOW_{session_id}_{nonce}"`) for
            OOB-based detections -- covers Batch 2's CMDi/XXE/
            Deserialization/SSRF/Host Header. `None` for every other
            vuln_type, including all of Batch 1.
    """

    vuln_type: str
    endpoint: str
    http_method: str
    parameter: str | None
    detected_by: str
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload_used: str | None = None
    raw_response_snapshot: str | None = None
    probe_correlation_id: str | None = None


class TriageResult(str, Enum):
    """Per-level AutonomousTriage outcome (Section 6.8's 9-state matrix).

    Applies independently to L1 (Replay), L2 (Variant), and L3 (Cross).
    Thresholds differ per level (Section 6.8's table) but the 3 possible
    OUTCOMES are the same enum for all three:
        L1 Replay:  3/3 identical -> PASS | exactly 2/3 -> INCONCLUSIVE | <=1/3 -> FAIL
        L2 Variant: >=2/3 signal  -> PASS | exactly 1/3  -> INCONCLUSIVE | 0/3   -> FAIL
        L3 Cross:   different scanner confirms -> PASS or INCONCLUSIVE (neutral;
                    never scored -- see compute_triage_score)
    """

    PASS = "pass"
    INCONCLUSIVE = "inconclusive"
    FAIL = "fail"


def compute_triage_score(l1: TriageResult, l2: TriageResult) -> int:
    """Scores AutonomousTriage's L1/L2 outcomes per Section 6.8.

    L3 (Cross) is deliberately excluded from the parameter list: Section
    6.8 states "L3 is always INCONCLUSIVE for scoring -- it doesn't affect
    score." L3's own PASS/INCONCLUSIVE result is still recorded (see
    EvidenceChain.triage_l3) for audit/reporting purposes; it simply never
    feeds this function.

    Args:
        l1: AutonomousTriage Level 1 (Replay) outcome.
        l2: AutonomousTriage Level 2 (Variant) outcome.

    Returns:
        3 if both PASS; 1 if exactly one of {L1, L2} is PASS (regardless
        of whether the other is INCONCLUSIVE or FAIL); 0 if neither is
        PASS. This is the complete 9-state matrix collapsed to 3 score
        buckets, exactly as Section 6.8 specifies:
            PASS+PASS=3 | PASS+INCONC=1 | PASS+FAIL=1
            INCONC+PASS=1 | INCONC+INCONC=0 | INCONC+FAIL=0
            FAIL+PASS=1 | FAIL+INCONC=0 | FAIL+FAIL=0
    """
    passes = sum(1 for r in (l1, l2) if r == TriageResult.PASS)
    return {2: 3, 1: 1, 0: 0}[passes]


@dataclass
class Reviewability:
    """Tracks the two independent report-eligibility gates (Section 3, v6.3).

    Attributes:
        passes_signal_gate: Set during Fast Lane from the EndpointSignals
            check (Section 3's findings.py comment). Week 1 cannot set
            this itself -- Fast Lane / the 29 scanners are Week 5-7 scope
            -- so it defaults to False (fail-closed) until a real Fast
            Lane run sets it.
        passes_blue_agent_gate: Set during Verification by blue_agent.py
            (Section 3's findings.py comment). blue_agent.py's real
            Gemini-backed verdict logic is Week 7 scope (sqli_verifier.py
            /xss_verifier.py/etc. land then, and blue_agent.py is listed
            alongside them in core/verifier/); defaults to False.
        signal_count: Raw count of Fast Lane signals that contributed to
            passes_signal_gate. Defaults to 0.
        blue_agent_verdict: One of "CONFIRMED", "DISPUTED", "REJECTED"
            (Section 3's Literal type, verbatim). The blueprint's Literal
            has no fourth "not yet reviewed" member, so this dataclass
            must pick a default for findings that haven't reached
            blue_agent.py yet. Default is "REJECTED" -- fail-closed,
            consistent with passes_blue_agent_gate defaulting False and
            with this project's scope_enforcer/safety_gate "hard gate,
            deny unless proven otherwise" posture throughout Section 10.
            Flag if a sentinel value (e.g. a 4th literal) is preferred
            instead -- see docs/DECISIONS.md Week 1 section.
    """

    passes_signal_gate: bool = False
    passes_blue_agent_gate: bool = False
    signal_count: int = 0
    blue_agent_verdict: Literal["CONFIRMED", "DISPUTED", "REJECTED"] = "REJECTED"


@dataclass
class EvidenceChain:
    """Section 5 (evidence types) + Section 6.8 (strength formula).

    Attributes:
        vuln_type: One of the 29 scanner-registry keys (e.g. "xss",
            "sqli"; Section 3.1's scanner column, lower_snake_case). Kept
            as a plain str rather than a new enum: no "VulnType" enum is
            named anywhere among Section 3's eleven ontology enums, and
            inventing one now would add ontology surface area the
            blueprint never asked for. The 29 valid values live in
            SCANNER_REGISTRY (Week 5), which is this field's real source
            of truth once it exists.
        collected_types: Every EvidenceType actually collected for this
            finding, in collection order (duplicates allowed here --
            e.g. a variant test that reconfirms `differential` twice --
            since `unique_collected_types` is what the counting rule
            actually uses).
        min_required: The per-vuln_type minimum from
            configs/vuln_thresholds.yaml's `min_evidence_types` (Section
            5.4). NOT looked up lazily inside this dataclass (ontology
            types do no file I/O) -- core/verifier/evidence_chain.py's
            build_evidence_chain() resolves and injects it at
            construction time. Defaults to 2, matching vuln_thresholds.yaml's
            own `default: 2` fallback (Section 5.4), so an EvidenceChain
            built without going through that factory still fails closed
            to a real, YAML-consistent number rather than 0.
        triage_l1: AutonomousTriage Level 1 (Replay) outcome, or None if
            not yet run.
        triage_l2: AutonomousTriage Level 2 (Variant) outcome, or None if
            not yet run.
        triage_l3: AutonomousTriage Level 3 (Cross) outcome, or None if
            not yet run. Recorded for audit/reporting; never affects
            triage_score (Section 6.8).
    """

    vuln_type: str
    collected_types: list[EvidenceType] = field(default_factory=list)
    min_required: int = 2
    triage_l1: TriageResult | None = None
    triage_l2: TriageResult | None = None
    triage_l3: TriageResult | None = None

    @property
    def unique_collected_types(self) -> set[EvidenceType]:
        """Section 5.2 COUNTING RULE: `len(set(collected_types))`."""
        return set(self.collected_types)

    @property
    def meets_per_vuln_minimum(self) -> bool:
        """Section 6.9: `evidence.evidence_chain.meets_per_vuln_minimum`.

        `meets_per_vuln_minimum = len(set(collected_types)) >= min_required`
        (Section 5.2, verbatim).
        """
        return len(self.unique_collected_types) >= self.min_required

    @property
    def triage_complete(self) -> bool:
        """True once all 3 AutonomousTriage levels have actually run.

        Used by deterministic_verifier.py to set Finding.evidence.verified
        (Section 6.8: "Set True... when all 3 triage levels completed AND
        SafeExploitHarness invoked"). Distinct from triage_score being
        computable: triage_score treats an unset level as FAIL (see
        below) so it never raises, but an incomplete triage must still
        block evidence.verified regardless of what score that produces --
        is_reportable requires evidence.verified, so an incomplete triage
        can never become reportable via this path.
        """
        return self.triage_l1 is not None and self.triage_l2 is not None and self.triage_l3 is not None

    @property
    def triage_score(self) -> int:
        """Section 6.8's 9-state score, via compute_triage_score(L1, L2).

        An unset (None) level is treated as FAIL for this calculation
        only -- a defined, non-crashing default (consistent with this
        blueprint's general philosophy of designing out undefined states
        rather than guarding them, e.g. Section 5.2's boolean_differential_confirmed
        redesign). This does NOT bypass the completeness gate:
        `triage_complete` (above) is what actually gates
        evidence.verified / is_reportable, not this score.
        """
        l1 = self.triage_l1 if self.triage_l1 is not None else TriageResult.FAIL
        l2 = self.triage_l2 if self.triage_l2 is not None else TriageResult.FAIL
        return compute_triage_score(l1, l2)

    @property
    def evidence_ratio(self) -> float:
        """Section 6.8: `min(len(unique_collected_types), 7) / 7.0`."""
        return min(len(self.unique_collected_types), _MAX_EVIDENCE_TYPES_FOR_RATIO) / float(
            _MAX_EVIDENCE_TYPES_FOR_RATIO
        )

    @property
    def triage_ratio(self) -> float:
        """Section 6.8: `triage_score / 3.0`."""
        return self.triage_score / _MAX_TRIAGE_SCORE

    @property
    def strength(self) -> float:
        """Section 6.8: `round(0.7 * evidence_ratio + 0.3 * triage_ratio, 3)`.

        Provably bounded to [0.0, 1.0] (v6.4-008 fix, verified in Section
        6.8's own commentary): evidence_ratio's cap at 7/7=1.0 and
        triage_ratio's cap at 3/3=1.0 make the maximum exactly
        0.7*1.0 + 0.3*1.0 = 1.0; the minimum (0 collected types,
        triage_score=0) is 0.0.
        """
        return round(_EVIDENCE_RATIO_WEIGHT * self.evidence_ratio + _TRIAGE_RATIO_WEIGHT * self.triage_ratio, 3)

    @property
    def strength_label(self) -> Literal["auto_report", "report_with_notes", "deep_verify", "low_confidence"]:
        """Section 6.8's advisory strength bands.

        ">=0.71 -> auto-report | >=0.43 -> report with notes | >=0.29 ->
        deep verify | <0.29 -> [LOW_CONFIDENCE]" (verbatim). "low_confidence"
        here is the plain-value counterpart of the `[LOW_CONFIDENCE]` log
        tag -- callers that want to emit the actual structured log line
        do so themselves (see core/verifier/deterministic_verifier.py);
        this property has no side effects, matching the rest of this
        ontology module.

        NOTE (Section 6.8, V6.4-L2 fix): this label is advisory
        report-quality guidance only. It is NOT a second reportability
        gate -- `Finding.is_reportable` is computed independently and is
        sole authority (Section 6.9 "Conflict resolution": is_reportable
        (FINAL AUTHORITY) -> strength (report quality, advisory) ->
        triage_score (input to strength, not a gate)). A finding can be
        is_reportable=True with strength_label="low_confidence", or
        is_reportable=False with strength_label="auto_report" -- the two
        are orthogonal.
        """
        s = self.strength
        if s >= _STRENGTH_AUTO_REPORT_THRESHOLD:
            return "auto_report"
        if s >= _STRENGTH_REPORT_WITH_NOTES_THRESHOLD:
            return "report_with_notes"
        if s >= _STRENGTH_DEEP_VERIFY_THRESHOLD:
            return "deep_verify"
        return "low_confidence"


@dataclass
class Evidence:
    """Section 6.9's PoC Gate inputs that live under `Finding.evidence`.

    Attributes:
        evidence_chain: The EvidenceChain backing this finding. Required
            (no default) -- an Evidence with no chain is not a
            meaningful object; every real Finding must construct its
            EvidenceChain first (typically via
            core/verifier/evidence_chain.py's build_evidence_chain()).
        exploit_executed: True once a SafeExploitHarness has actually run
            against this finding, independent of whether the specific
            probe signal fired (Section 7.1, V6.4-M4 fix: "SafeExploitHarness
            for XSS always runs Playwright to set exploit_executed=True;
            dom_execution_confirmed is set only if the probe string
            appears... these are independent flags, not the same
            condition"). Defaults False (fail-closed).
        exploit_safe: True if the technique used was non-destructive per
            Section 10.1's TIER_C_RULES auto_allow list. Defaults False
            (fail-closed).
        verified: Section 6.8's `evidence.verified` -- "Set True by
            deterministic_verifier.py when all 3 triage levels completed
            AND SafeExploitHarness invoked. Means the workflow ran; not
            that the vulnerability is confirmed." Defaults False; only
            deterministic_verifier.py (Week 1) sets this True.
    """

    evidence_chain: EvidenceChain
    exploit_executed: bool = False
    exploit_safe: bool = False
    verified: bool = False


@dataclass
class Finding:
    """Section 3's ontology/findings.py `Finding` type + Section 6.9 PoC Gate.

    ExploitCandidate and PoC (also named in Section 3's findings.py
    comment) are deferred -- see this module's header docstring.

    Attributes:
        finding_id: Stable identifier for this finding (dedup key
            construction, cross-referencing in reports; the Section 6.9
            DEDUP_KEY 4-tuple itself is Week 7 scope, not built here).
        vuln_type: Same 29-scanner-registry-key convention as
            EvidenceChain.vuln_type (kept consistent between the two
            rather than only storing it once, since a Finding could in
            principle reference evidence assembled by another
            component -- duplicating the key costs one field and buys
            a cheap consistency check callers can assert on).
        endpoint: The endpoint path this finding concerns (Section 6.9's
            DEDUP_KEY uses `endpoint_path`; full URL vs. path-only
            normalization is Week 7's dedup-key concern, not this
            dataclass's).
        evidence: The Evidence object (Section 6.9's PoC Gate reads
            `evidence.*` off this).
        reviewability: Defaults to a fresh, all-False/zero Reviewability
            (fail-closed) via default_factory -- appropriate here since
            Week 1 does not build Fast Lane or blue_agent.py, the two
            components that actually populate this field's gates.
    """

    finding_id: str
    vuln_type: str
    endpoint: str
    evidence: Evidence
    reviewability: Reviewability = field(default_factory=Reviewability)

    @property
    def is_reportable(self) -> bool:
        """Section 6.9's PoC Gate, verbatim (`finding`/`evidence` -> `self`/`self.evidence`).

        ```
        is_reportable: bool = (
            evidence.exploit_executed
            and evidence.exploit_safe
            and finding.reviewability.passes_signal_gate
            and evidence.evidence_chain.meets_per_vuln_minimum
            and finding.reviewability.passes_blue_agent_gate
            and evidence.verified
        )
        ```

        This is the ONLY reportability gate (Section 6.9 "Conflict
        resolution": is_reportable is FINAL AUTHORITY). EvidenceChain.strength
        / strength_label are advisory report-quality signals computed
        independently and never feed this property.

        Note on file placement: Section 3's file tree names no dedicated
        "PoC Gate" module. This is implemented as a property here --
        a pure, side-effect-free boolean derived entirely from Finding's
        own state -- rather than inventing an unnamed core/verifier/poc_gate.py.
        Placement choice, not a blueprint citation; flag if a dedicated
        module is preferred instead. See docs/DECISIONS.md Week 1 section.
        """
        evidence = self.evidence
        return (
            evidence.exploit_executed
            and evidence.exploit_safe
            and self.reviewability.passes_signal_gate
            and evidence.evidence_chain.meets_per_vuln_minimum
            and self.reviewability.passes_blue_agent_gate
            and evidence.verified
        )
