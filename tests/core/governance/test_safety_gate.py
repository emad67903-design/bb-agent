"""
Implements: Section 10.1 / Section 10.6 test coverage --
core/governance/safety_gate.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import pytest

from core.governance.safety_gate import (
    ApprovalManagerUnavailable,
    TierCapExceeded,
    enforce_vdp_tier_cap,
    route_tier_d_action,
)
from core.ontology.enums import TierLevel


class _FakeApprovalManager:
    """Test double satisfying ApprovalManagerProtocol -- the real
    approval_manager.py doesn't exist yet (module docstring)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def submit_for_approval(self, *, vuln_type: str, session_id: str, action_description: str) -> str:
        self.calls.append(
            {"vuln_type": vuln_type, "session_id": session_id, "action_description": action_description}
        )
        return f"approval-{len(self.calls)}"


class TestEnforceVdpTierCap:
    def test_bug_bounty_tier_c_is_not_capped(self):
        enforce_vdp_tier_cap(TierLevel.TIER_C, "bug_bounty")  # must not raise

    def test_bug_bounty_tier_d_is_not_capped_by_this_function(self):
        """VDP cap and TIER_D-always-human are separate rules (module
        docstring) -- this function only enforces the VDP-specific cap."""
        enforce_vdp_tier_cap(TierLevel.TIER_D, "bug_bounty")  # must not raise

    def test_vdp_tier_a_is_allowed(self):
        enforce_vdp_tier_cap(TierLevel.TIER_A, "vdp")  # must not raise

    def test_vdp_tier_b_is_allowed(self):
        enforce_vdp_tier_cap(TierLevel.TIER_B, "vdp")  # must not raise

    def test_vdp_tier_c_raises(self):
        with pytest.raises(TierCapExceeded, match="TIER_B"):
            enforce_vdp_tier_cap(TierLevel.TIER_C, "vdp")

    def test_vdp_tier_d_raises(self):
        with pytest.raises(TierCapExceeded):
            enforce_vdp_tier_cap(TierLevel.TIER_D, "vdp")

    def test_message_text_matches_section_10_1_code_block_verbatim(self):
        """Message-text arbitration (Week 2 continuation prompt): Section
        10.1's code block version is authoritative over Section 3's tree
        comment, per the explicit instruction to prefer code blocks."""
        received = []
        with pytest.raises(TierCapExceeded):
            enforce_vdp_tier_cap(TierLevel.TIER_C, "vdp", notifier=received.append)
        assert received == ["VDP_BLOCKED: Tier C/D action attempted on VDP target"]

    def test_notifier_not_called_when_no_violation(self):
        received = []
        enforce_vdp_tier_cap(TierLevel.TIER_A, "vdp", notifier=received.append)
        assert received == []

    def test_notifier_none_by_default_does_not_crash(self):
        with pytest.raises(TierCapExceeded):
            enforce_vdp_tier_cap(TierLevel.TIER_C, "vdp")  # no notifier passed


class TestRouteTierDAction:
    def test_routes_to_approval_manager_and_returns_id(self):
        manager = _FakeApprovalManager()
        request_id = route_tier_d_action(
            TierLevel.TIER_D,
            vuln_type="cmd_injection",
            session_id="sess-1",
            action_description="test action",
            approval_manager=manager,
        )
        assert request_id == "approval-1"
        assert manager.calls == [
            {"vuln_type": "cmd_injection", "session_id": "sess-1", "action_description": "test action"}
        ]

    def test_raises_when_approval_manager_is_none(self):
        with pytest.raises(ApprovalManagerUnavailable):
            route_tier_d_action(
                TierLevel.TIER_D,
                vuln_type="cmd_injection",
                session_id="sess-1",
                action_description="test action",
                approval_manager=None,
            )

    @pytest.mark.parametrize("tier", [TierLevel.TIER_A, TierLevel.TIER_B, TierLevel.TIER_C])
    def test_raises_value_error_for_non_tier_d(self, tier):
        manager = _FakeApprovalManager()
        with pytest.raises(ValueError, match="TIER_D"):
            route_tier_d_action(
                tier,
                vuln_type="xss",
                session_id="sess-1",
                action_description="wrong tier",
                approval_manager=manager,
            )

    def test_never_autonomous_even_without_vdp(self):
        """Section 10.1: TIER_D is permanent and unconditional -- not
        VDP-specific. Confirms route_tier_d_action still requires a real
        approval_manager for a bug_bounty-program session too."""
        with pytest.raises(ApprovalManagerUnavailable):
            route_tier_d_action(
                TierLevel.TIER_D,
                vuln_type="deserialization",
                session_id="sess-2",
                action_description="gadget execution beyond OOB",
                approval_manager=None,
            )
