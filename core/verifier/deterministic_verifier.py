"""
Implements: Section 3 -- core/verifier/deterministic_verifier.py
  ("Sets Finding.reviewability AND Finding.evidence.verified")
Also implements: Section 6.8 (`evidence.verified` completeness flag,
`[LOW_CONFIDENCE]` strength-band logging).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 1 SCOPE: only the `evidence.verified` half of Section 3's "Sets
Finding.reviewability AND Finding.evidence.verified" comment is
implemented here. `Finding.reviewability`'s two gates are populated
elsewhere and by components that don't exist yet -- `passes_signal_gate`
by Fast Lane (Week 5) from the EndpointSignals check, `passes_blue_agent_gate`
by `blue_agent.py` (Week 7). `verify_finding` below does not touch
`finding.reviewability` at all; it is passed through unchanged. See
docs/DECISIONS.md Week 1 section, item 16.
"""

from __future__ import annotations

import logging

from core.ontology.findings import Finding

logger = logging.getLogger(__name__)


def verify_finding(finding: Finding, harness_invoked: bool) -> Finding:
    """Sets `finding.evidence.verified` per Section 6.8, in place.

    Section 6.8, verbatim: "`evidence.verified` flag: Set True by
    `deterministic_verifier.py` when all 3 triage levels completed AND
    SafeExploitHarness invoked. Means the workflow ran; not that the
    vulnerability is confirmed."

    Also logs `[LOW_CONFIDENCE]` when the finding's EvidenceChain.strength
    falls in that band (Section 6.8, V6.4-L2 fix) -- an advisory,
    report-quality signal, explicitly NOT equivalent to
    `is_reportable=False` (Section 6.9 "Conflict resolution": is_reportable
    is the sole reporting authority; strength/strength_label never gate
    it).

    Args:
        finding: The Finding to verify. Mutated in place (Finding and
            Evidence are non-frozen dataclasses) and also returned, so
            callers can use either the return value or the original
            reference.
        harness_invoked: True if a SafeExploitHarness actually ran
            against this finding (Section 6.8: harness invocation is
            required regardless of whether the harness's own probe
            signal fired -- see Section 7.1's V6.4-M4 fix on
            `exploit_executed` vs. `dom_execution_confirmed` being
            independent flags).

    Returns:
        The same Finding instance, with `evidence.verified` set.
    """
    chain = finding.evidence.evidence_chain
    finding.evidence.verified = chain.triage_complete and harness_invoked

    if chain.strength_label == "low_confidence":
        logger.info(
            "[LOW_CONFIDENCE] finding_id=%s vuln_type=%s strength=%.3f -- "
            "queued for additional testing; NOT equivalent to is_reportable=False (Section 6.8)",
            finding.finding_id,
            chain.vuln_type,
            chain.strength,
        )

    return finding
