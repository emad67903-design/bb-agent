"""
Implements: Section 2 Layer 3 / Section 1.4 test coverage --
core/cognitive/persona_router.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import ast

import pytest

import core.cognitive.persona_router as persona_router_module
from core.cognitive.persona_router import Persona, PersonaName, PersonaRouter

_EXPECTED_NAMES = {
    "xss_expert",
    "recon_architect",
    "chain_strategist",
    "business_logic",
    "root_hacker",
}


class TestPersonaName:
    def test_has_exactly_five_members(self):
        assert len(PersonaName) == 5

    def test_values_match_section_2_layer_3_exactly(self):
        assert {p.value for p in PersonaName} == _EXPECTED_NAMES


class TestSingleModelInvariant:
    """Section 1.4 principle 1 / Section 13: 'Second local model' is a
    permanently rejected item. These tests pin that no persona can ever
    carry a different model string."""

    def test_all_five_personas_share_the_same_model_string(self):
        router = PersonaRouter()
        models = {router.select(name).model for name in PersonaName}
        assert len(models) == 1

    def test_model_matches_token_throttlers_own_documented_model(self):
        """Cross-checks against core/governance/token_throttler.py's own
        docstring text ('Records one local qwen2.5-coder:7b call') rather
        than just re-asserting the same literal by hand twice -- if that
        file's model reference ever changes, this test catches the drift."""
        import inspect

        import core.governance.token_throttler as throttler_module

        throttler_source = inspect.getsource(throttler_module)
        router = PersonaRouter()
        model = router.select(PersonaName.XSS_EXPERT).model
        assert model in throttler_source

    def test_persona_dataclass_has_no_model_override_parameter_exposed_by_router(self):
        """PersonaRouter.select()/switch_to() take only a name -- there is
        no path for a caller to request a persona with a different model."""
        import inspect

        assert list(inspect.signature(PersonaRouter.select).parameters) == ["self", "name"]
        assert list(inspect.signature(PersonaRouter.switch_to).parameters) == ["self", "name"]

    def test_qwen_7b_not_3b(self):
        """This session's confirmed licensing fact (Qwen Research Licence
        on 3b is non-commercial; 7b is Apache 2.0, Section 1.3/13) --
        pinned here too since PersonaRouter is exactly the component a
        stale '3b' reference would most plausibly leak back into."""
        router = PersonaRouter()
        model = router.select(PersonaName.XSS_EXPERT).model
        assert model == "qwen2.5-coder:7b"
        assert "3b" not in model


class TestPersonaRouterIsPureSelection:
    """This class does not call an LLM and does not touch
    token_throttler.py -- see module docstring. Verified structurally
    (no matching import anywhere in the module's AST), not just by
    absence-of-evidence in the tests above."""

    def test_module_has_no_llm_calling_or_throttler_imports(self):
        tree = ast.parse(inspect_source())
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)

        forbidden_substrings = ("token_throttler", "ollama", "requests", "httpx", "aiohttp", "socket", "subprocess")
        for name in imported_names:
            for forbidden in forbidden_substrings:
                assert forbidden not in name, f"persona_router.py imports {name!r} -- should be pure selection"

    def test_module_has_no_network_or_subprocess_calls_in_source_text(self):
        source = inspect_source()
        for forbidden in ("socket.", "subprocess.", "requests.", "httpx.", "urllib.request"):
            assert forbidden not in source


def inspect_source() -> str:
    import inspect

    return inspect.getsource(persona_router_module)


class TestPersonaRouterSelection:
    def test_select_returns_matching_persona(self):
        router = PersonaRouter()
        p = router.select(PersonaName.XSS_EXPERT)
        assert p.name == PersonaName.XSS_EXPERT

    def test_select_does_not_change_active_persona(self):
        router = PersonaRouter()
        original_active = router.active_persona.name
        router.select(PersonaName.ROOT_HACKER)
        assert router.active_persona.name == original_active

    def test_switch_to_changes_active_persona(self):
        router = PersonaRouter()
        router.switch_to(PersonaName.CHAIN_STRATEGIST)
        assert router.active_persona.name == PersonaName.CHAIN_STRATEGIST

    def test_switch_to_returns_the_new_persona(self):
        router = PersonaRouter()
        result = router.switch_to(PersonaName.BUSINESS_LOGIC)
        assert result.name == PersonaName.BUSINESS_LOGIC

    def test_default_active_persona_is_recon_architect(self):
        """Section 6.3: Phase 2 (Mental Model Builder) is the first phase
        to use any persona at all."""
        router = PersonaRouter()
        assert router.active_persona.name == PersonaName.RECON_ARCHITECT

    def test_all_personas_returns_all_five_in_declaration_order(self):
        router = PersonaRouter()
        result = router.all_personas()
        assert len(result) == 5
        assert [p.name for p in result] == list(PersonaName)

    def test_each_persona_has_nonempty_system_prompt(self):
        router = PersonaRouter()
        for persona in router.all_personas():
            assert isinstance(persona.system_prompt, str)
            assert len(persona.system_prompt) > 0

    def test_persona_is_frozen(self):
        p = Persona(PersonaName.XSS_EXPERT, "prompt text")
        with pytest.raises(AttributeError):
            p.system_prompt = "changed"  # type: ignore[misc]

    def test_two_router_instances_are_independent(self):
        """Switching one instance's active persona must not affect another."""
        r1 = PersonaRouter()
        r2 = PersonaRouter()
        r1.switch_to(PersonaName.ROOT_HACKER)
        assert r2.active_persona.name == PersonaName.RECON_ARCHITECT
