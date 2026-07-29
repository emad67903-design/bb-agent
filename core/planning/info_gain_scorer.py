"""
Implements: Section 3 -- core/planning/info_gain_scorer.py ("Computes
BusinessValue from MentalModel assumptions").
Also implements: Section 6.7's exact scoring formula and Section 3's
`enums.py` comment ("BusinessValue... Computed by info_gain_scorer.py
from MentalModel assumptions").
Blueprint: bb_agent_v6.6_final_blueprint.md

FULLY SPECIFIED, not provisional -- unlike `target_adapter.py` and
`campaign_planner.py` in this same week's batch. Section 6.7 gives the
exact formula this module implements:

    "BusinessValue computed by info_gain_scorer.py: HIGH if
    max(exploitability_score of relevant assumptions) >= 0.7; MEDIUM if
    0.4 <= max < 0.7; LOW if max < 0.4."

`BusinessValue.UNKNOWN` (docs/DECISIONS.md item 27) is explicitly
excluded from this function's possible return values -- it is reserved
solely for Week 4's BeliefGraph-deserialization fallback (Section
11.3's R-L4 fix) and has no corresponding threshold branch here, by
design (see `core/ontology/enums.py`'s `BusinessValue` docstring).

"RELEVANT ASSUMPTIONS" -- ONE INTERPRETIVE CHOICE, DOCUMENTED: Section
6.7 never defines what makes an assumption "relevant" versus not (e.g.
relevant to a specific `BeliefGraph` node/hypothesis, once one exists in
Week 4). Rather than inventing a relevance-filtering rule with no
citation, this module accepts an already-filtered `list[Assumption]`
directly -- the CALLER decides what "relevant" means for their context
(this week: the natural, simplest reading is "all assumptions in the
current `MentalModel`," since there is no `BeliefGraph` yet to filter
by; `score_mental_model` below does exactly that). If Week 4 introduces
a real per-hypothesis relevance filter, it filters before calling
`score_assumptions`, rather than this module growing an opinion about
what "relevant" means.
"""

from __future__ import annotations

from core.ontology.enums import BusinessValue
from core.ontology.mental_model import Assumption, MentalModel


def score_assumptions(assumptions: list[Assumption]) -> BusinessValue:
    """Section 6.7's exact formula: HIGH if max(exploitability_score) >=
    0.7; MEDIUM if 0.4 <= max < 0.7; LOW if max < 0.4.

    Args:
        assumptions: The assumptions to score over -- the caller's
            already-decided "relevant" set (see module docstring).

    Returns:
        `BusinessValue.LOW`, `.MEDIUM`, or `.HIGH`. Never
        `.UNKNOWN` -- that member has no threshold branch here by
        design (module docstring); it is Week 4's deserialization
        fallback only.

    Raises:
        ValueError: If `assumptions` is empty -- there is no
            `max()` to take, and Section 6.7 gives no defined score for
            "zero assumptions." Rather than silently returning a
            default (which threshold would it be? none is cited), this
            fails loudly so a caller notices an empty-`MentalModel`
            edge case rather than getting a silently-wrong score.
    """
    if not assumptions:
        raise ValueError(
            "score_assumptions() requires at least one assumption; Section 6.7 "
            "gives no defined BusinessValue for an empty assumption set."
        )
    max_score = max(a.exploitability_score for a in assumptions)
    if max_score >= 0.7:
        return BusinessValue.HIGH
    if max_score >= 0.4:
        return BusinessValue.MEDIUM
    return BusinessValue.LOW


def score_mental_model(mental_model: MentalModel) -> BusinessValue:
    """Convenience wrapper: scores ALL of `mental_model.assumptions`.

    This week's simplest reading of "relevant assumptions" (module
    docstring) -- every assumption in the given `MentalModel`, since no
    `BeliefGraph` (Week 4) exists yet to narrow the set further.

    Args:
        mental_model: The `MentalModel` whose assumptions to score.

    Returns:
        Same as `score_assumptions`.

    Raises:
        ValueError: If `mental_model.assumptions` is empty (see
            `score_assumptions`).
    """
    return score_assumptions(mental_model.assumptions)
