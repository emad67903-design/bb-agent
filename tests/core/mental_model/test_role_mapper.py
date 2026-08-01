"""
Implements: test coverage -- core/mental_model/role_mapper.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from core.mental_model.role_mapper import extract_roles
from core.ontology.mental_model import FlowSignal, PageSignals, RoleSignal


class TestExtractRoles:
    def test_returns_the_page_signals_role_list(self):
        signals = PageSignals(
            page_url="https://target.test/",
            role_signals=[RoleSignal(role="admin", evidence="/admin/ link")],
            flow_signals=[FlowSignal(description="d", evidence="e")],
        )
        assert extract_roles(signals) == [RoleSignal(role="admin", evidence="/admin/ link")]

    def test_does_not_make_its_own_call(self):
        """No ollama import in this module at all -- it must be a pure
        reader, never a second call site (mental_model_builder_prompt_
        design.md Section 1: one combined call per page)."""
        import core.mental_model.role_mapper as role_mapper_module

        assert "ollama" not in dir(role_mapper_module)

    def test_empty_role_signals_returns_empty_list(self):
        signals = PageSignals(page_url="https://target.test/")
        assert extract_roles(signals) == []
