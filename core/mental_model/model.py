"""
Implements: Section 3 -- core/mental_model/model.py ("MentalModel
itself").
Blueprint: bb_agent_v6.6_final_blueprint.md

Thin re-export, per the ontology-placement resolution (docs/DECISIONS.md
item 29): `MentalModel` and `Assumption` are defined once, in
`core/ontology/mental_model.py`, per the Engineering Constitution's
ontology-first rule. This module exists so Section 3's file tree is
preserved -- other `core/mental_model/` files (`builder.py`,
`role_mapper.py`, `flow_tracer.py`, `boundary_identifier.py`,
`assumption_extractor.py`, `exploitability_scorer.py`) import from
`core.mental_model.model`, matching the directory Section 3 places them
in, without a second, duplicate definition of either type existing
anywhere.
"""

from __future__ import annotations

from core.ontology.mental_model import (
    Assumption,
    FlowSignal,
    MentalModel,
    PageSignals,
    RoleSignal,
)

__all__ = ["Assumption", "FlowSignal", "MentalModel", "PageSignals", "RoleSignal"]
