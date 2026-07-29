"""
Implements: Section 6.4 test coverage -- core/planning/target_adapter.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from core.ontology.enums import TargetType
from core.planning.target_adapter import TargetAdapter


class TestTargetAdapter:
    def test_defaults_to_unknown_target_type(self):
        adapter = TargetAdapter()
        assert adapter.target_type is TargetType.UNKNOWN

    def test_holds_the_supplied_target_type(self):
        adapter = TargetAdapter(target_type=TargetType.GRAPHQL)
        assert adapter.target_type is TargetType.GRAPHQL

    def test_adjust_weights_is_identity_this_week(self):
        """PROVISIONAL, documented: no per-TargetType adjustment table
        is cited anywhere in the blueprint (module docstring). Pins the
        current, honest behavior so a future real implementation
        changes this test deliberately, not by silent regression."""
        adapter = TargetAdapter(target_type=TargetType.ECOMMERCE)
        base = {"xss_scanner": 0.65, "race_scanner": 0.30}
        assert adapter.adjust_weights(base) == base

    def test_adjust_weights_identity_holds_for_every_target_type(self):
        base = {"sqli_scanner": 0.55}
        for target_type in TargetType:
            adapter = TargetAdapter(target_type=target_type)
            assert adapter.adjust_weights(base) == base

    def test_adjust_weights_returns_a_copy_not_the_same_object(self):
        adapter = TargetAdapter()
        base = {"xss_scanner": 0.65}
        result = adapter.adjust_weights(base)
        assert result == base
        assert result is not base
        result["xss_scanner"] = 0.99
        assert base["xss_scanner"] == 0.65, "mutating the result must not mutate the caller's baseline"

    def test_is_frozen(self):
        import pytest

        adapter = TargetAdapter()
        with pytest.raises(AttributeError):
            adapter.target_type = TargetType.SPA  # type: ignore[misc]
