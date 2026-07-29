"""
Implements: Section 3 -- core/planning/target_adapter.py.
Also implements: Section 6.4 ("`CampaignPlanner` uses `MentalModel` +
`TargetAdapter` (TargetType enum)").
Blueprint: bb_agent_v6.6_final_blueprint.md

PROVISIONAL (rule B, per the advisor Claude's consolidated directive):
Section 6.4 gives exactly one sentence about this component -- "uses
`MentalModel` + `TargetAdapter` (`TargetType` enum)... Output: ordered
test plan and scanner weights" -- with no method signature, no
per-`TargetType` weight table, and no algorithm anywhere in the
blueprint's 16 sections (grep-confirmed: `TargetAdapter` appears at
this one location, total). This is a `MentalModel`-shaped gap (content
genuinely unspecified), not a `scope_enforcer.py`-shaped one (content
specified, week tag missing) -- treated accordingly: built as a
minimal, honestly-inert interface, not populated with invented
per-scanner-per-target-type numbers.

WHAT THIS DELIBERATELY DOES NOT DO: adjust weights differently per
`TargetType`. No blueprint section gives a single concrete adjustment
(e.g. "on `GRAPHQL` targets, boost `graphql_scanner` weight by X, cut
`csrf_scanner` by Y") for any of the 7 `TargetType` members. Inventing
a full 7-type x 29-scanner adjustment table with no citation would be
exactly the kind of magic-number fabrication the Engineering
Constitution's "CONFIG-DRIVEN, ZERO MAGIC NUMBERS" rule exists to
prevent -- doubly so since these would be BEHAVIORAL weights, not a
formatting choice. `adjust_weights` below is the identity function,
explicitly and testably so, until a real weight table exists somewhere
citable (`vuln_weights.yaml` already holds `starting_weights` per vuln
type, Section 9.5 -- a per-`TargetType` OVERLAY on top of those, if one
is ever specified, belongs there, not invented in this file).

The one thing this file DOES do non-trivially: hold and expose the
target's classified `TargetType`, which `CampaignPlanner` reads. Even
that classification logic -- how a real target actually GETS assigned
a `TargetType` from recon signals -- is out of scope here too:
`ReconState` (Section 3, `core/ontology/state.py`) does not exist yet
in this repository, so there is nothing yet to classify FROM. Callers
construct a `TargetAdapter` with an already-known `TargetType` (e.g.
supplied directly, or defaulted to `TargetType.UNKNOWN`) rather than
this module inferring it.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.ontology.enums import TargetType


@dataclass(frozen=True)
class TargetAdapter:
    """PROVISIONAL (see module docstring). Holds a target's classified
    `TargetType` and exposes the (currently identity) weight-adjustment
    hook `CampaignPlanner` calls.

    Attributes:
        target_type: The target's classified type. Defaults to
            `TargetType.UNKNOWN` when no classification is available
            yet -- `TargetType.UNKNOWN` exists in the enum for exactly
            this case (Section 3's 7-member list).
    """

    target_type: TargetType = TargetType.UNKNOWN

    def adjust_weights(self, base_weights: dict[str, float]) -> dict[str, float]:
        """Returns scanner weights adjusted for this target's type.

        PROVISIONAL: currently the identity function for every
        `TargetType`, including `UNKNOWN` -- see module docstring for
        why no per-type adjustment table is invented here. Returns a
        new dict (a copy of `base_weights`), not the same object, so a
        caller mutating the result never mutates the caller's own
        baseline weights.

        Args:
            base_weights: Scanner-id -> weight mapping, e.g. sourced
                from `vuln_weights.yaml`'s `starting_weights` (Section
                9.5) or `CalibrationGuardian`'s calibrated weights.

        Returns:
            A dict with the same keys and values as `base_weights`
            (identity adjustment, this week).
        """
        return dict(base_weights)
