"""
Implements: Section 3 test coverage -- core/mental_model/model.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from core.mental_model import model as mental_model_reexport
from core.ontology import mental_model as ontology_mental_model


class TestModelIsAThinReexport:
    """docs/DECISIONS.md item 29: MentalModel/Assumption are defined
    ONCE, in core/ontology/mental_model.py. This module must re-export,
    never redefine."""

    def test_mentalmodel_is_the_identical_class_object(self):
        assert mental_model_reexport.MentalModel is ontology_mental_model.MentalModel

    def test_assumption_is_the_identical_class_object(self):
        assert mental_model_reexport.Assumption is ontology_mental_model.Assumption

    def test_reexport_is_usable_directly(self):
        instance = mental_model_reexport.MentalModel(business_purpose="p")
        assert isinstance(instance, ontology_mental_model.MentalModel)
