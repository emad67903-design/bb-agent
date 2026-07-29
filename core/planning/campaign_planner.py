"""
Implements: Section 3 -- core/planning/campaign_planner.py.
Also implements: Section 6.4 ("Phase 3: Campaign Planning... uses
`MentalModel` + `TargetAdapter`... Output: ordered test plan and
scanner weights") and, loosely, Section 8.1's integration-pattern line
("CampaignPlanner -> ReconSubgraph: ReconConfig" -- see the naming note
below).
Blueprint: bb_agent_v6.6_final_blueprint.md

PROVISIONAL (rule B): Section 6.4 is the only description of this
component's behavior in the entire blueprint -- one sentence, no field
list for its output, no ordering algorithm. `CampaignPlan` below (this
week's name for that output) is this session's own naming; Section
8.1's separate integration-pattern line calls the same conceptual
hand-off "ReconConfig" instead ("CampaignPlanner -> ReconSubgraph:
ReconConfig"). Not reconciled as a hard stop (rule A) because nothing
CONTRADICTS -- Section 8.1 never gives ReconConfig a field list either,
so there's no incompatible value being asserted for the same field,
just two different informal names for an output nothing yet consumes
downstream (`ReconSubgraph` doesn't exist yet in this repository). If a
real `ReconSubgraph`/`ReconConfig` consumer is built in a later week and
expects specific field names, reconcile then, against that consumer's
actual needs, rather than guessing now.

`CampaignPlan` is placed HERE (`core/planning/`), not
`core/ontology/`, unlike `MentalModel` (docs/DECISIONS.md item 29) and
`BrowserCapture` (item 32) -- deliberately different, not an
inconsistency repeat: Section 3 never associates this output type with
`core/ontology/` in any way (no tree comment, no field-in-a-state-object
phrasing), unlike `MentalModel` (explicitly a `ParentState` field per
`state.py`'s own comment) or `BrowserCapture` (at least a named return
type in a comment block). Its only cited consumer, `ReconSubgraph`, is
single and doesn't exist yet -- the same low-blast-radius, single-file
shape Week 2 used for `WebhookEvent`/`PersonaName`
(docs/DECISIONS.md items 21/23), not the ontology-placement shape.

ORDERING ALGORITHM -- PROVISIONAL, invented, flagged as such: "ordered
test plan" is taken to mean "scanners ordered by (adjusted) weight,
descending" -- the simplest reading consistent with the one cited
sentence, since no other ordering criterion (e.g. by `MentalModel`
`business_value`, by role/boundary proximity) is specified anywhere.
`MentalModel` is accepted as a parameter and its presence is required
(matching Section 6.4's "uses `MentalModel` + `TargetAdapter`"
literally) but this week's ordering logic does not yet read any of its
fields -- there is no cited rule for HOW `MentalModel` content should
influence ordering, only that the component "uses" it. Flagged rather
than silently invented: a plausible-sounding rule (e.g. "boost scanners
relevant to identified trust boundaries") would be exactly the kind of
guess rule (B) exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.ontology.mental_model import MentalModel
from core.planning.target_adapter import TargetAdapter


@dataclass(frozen=True)
class CampaignPlan:
    """PROVISIONAL (see module docstring). This week's name for Section
    6.4's "ordered test plan and scanner weights" output.

    Attributes:
        ordered_scanner_ids: Scanner-registry keys, ordered by adjusted
            weight descending -- the order Fast Lane should run them
            in. `SCANNER_REGISTRY` doesn't exist yet in this repository
            (Week 5, per Section 12), so these are plain strings, not
            resolved scanner instances or a registry lookup.
        scanner_weights: Scanner-id -> weight, after
            `TargetAdapter.adjust_weights` -- the "scanner weights"
            half of Section 6.4's output.
        mental_model: The `MentalModel` this plan was built from. Kept
            on the plan (not just consumed and discarded) so a caller
            can inspect what informed it -- not itself read by this
            week's ordering logic (see module docstring).
    """

    ordered_scanner_ids: list[str] = field(default_factory=list)
    scanner_weights: dict[str, float] = field(default_factory=dict)
    mental_model: MentalModel | None = None


def build_campaign_plan(
    mental_model: MentalModel,
    target_adapter: TargetAdapter,
    base_scanner_weights: dict[str, float],
) -> CampaignPlan:
    """Produces a `CampaignPlan` from a `MentalModel` and `TargetAdapter`
    (Section 6.4).

    PROVISIONAL ordering rule (module docstring): scanners ordered by
    `target_adapter`-adjusted weight, descending. `mental_model` is
    required (Section 6.4's literal "uses `MentalModel` +
    `TargetAdapter`") and carried on the returned `CampaignPlan`, but
    does not yet influence ordering -- no cited rule specifies how it
    should.

    Args:
        mental_model: This session's built `MentalModel` (Section 6.3's
            Phase 2 output).
        target_adapter: This target's `TargetAdapter` (Section 6.4).
        base_scanner_weights: Scanner-id -> weight before target-type
            adjustment, e.g. `vuln_weights.yaml`'s `starting_weights`
            (Section 9.5).

    Returns:
        A `CampaignPlan` with scanners ordered by adjusted weight,
        descending.
    """
    adjusted = target_adapter.adjust_weights(base_scanner_weights)
    ordered_ids = sorted(adjusted, key=lambda scanner_id: adjusted[scanner_id], reverse=True)
    return CampaignPlan(
        ordered_scanner_ids=ordered_ids,
        scanner_weights=adjusted,
        mental_model=mental_model,
    )
