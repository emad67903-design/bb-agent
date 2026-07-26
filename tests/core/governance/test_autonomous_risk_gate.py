"""
Implements: Section 10.1 / v6.4-004 test coverage -- core/governance/autonomous_risk_gate.py
Blueprint: bb_agent_v6.6_final_blueprint.md

The `auto_allow`/`human_required` lists below are hard-pinned exact
copies of Section 10.1, verified by a real programmatic diff against
the blueprint source at implementation time (not eyeballed) -- see
docs/DECISIONS.md, Week 2 section. This test file re-pins the same
values as a regression guard: if TIER_C_RULES in the source module ever
drifts (a reword, a reorder, a dropped entry), this test catches it
without needing the blueprint file present in the test environment.
"""

from __future__ import annotations

import pytest

from core.governance.autonomous_risk_gate import (
    AUTO_ALLOW_BY_VULN_TYPE,
    TIER_C_RULES,
    AutonomousRiskGate,
    RiskDecision,
)
from core.ontology.enums import TierLevel

_EXPECTED_AUTO_ALLOW = [
    "SQLi read-only UNION SELECT probe (test account)",
    "SSTI expression evaluation {{7*7}} only (no RCE, no file read)",
    "LFI /etc/hostname read (non-sensitive, Linux only)",
    "Path traversal /etc/hostname or win.ini (non-sensitive)",
    "CRLF X-XBOW header injection (non-sensitive)",
    "XXE OOB callback or /etc/hostname (non-sensitive)",
    "CMDi OOB interactsh ping only (no destructive command)",
    "Deserialization OOB callback only (no gadget execution)",
    "XSS benign console.log probe (test account)",
    "CSRF proof on non-sensitive action (test account)",
    "Open redirect to example.com",
    "Prototype pollution __proto__[xbow_probe] key only",
    "CORS origin reflection check (read-only)",
    "IDOR cross-account read: verify boundary; discard (test accounts)",
    "BAC cross-privilege read: verify boundary; discard (test accounts)",
    "JWT alg:none or weak secret probe (test account)",
    "OAuth redirect_uri probe to xbow-probe.com",
    "Mass assignment extra field + readback (test account)",
    "Auth state machine: fixation/timeout/lockout tests",
    "GraphQL introspection dump (read-only schema)",
    "API versioning older endpoint read (read-only)",
    "SSRF metadata read (IMDSv2 two-step if method override possible; "
    "else IMDS_V2_ENFORCED signal; body suppressed from Redis)",
    "Host header OOB or error-message leak only",
    "Race coupon redemption (test account, non-financial)",
    "WebSocket auth-bypass check or benign injection probe (test account, read-only)",
    "HTTP smuggling timing differential (no state change)",
    "Hardcoded credentials API validation via allowlist (GET only)",
    "Nuclei Tier A/B templates only",
    "Business logic workflow-assumption violation on test account "
    "(e.g., coupon-after-item-removal), non-destructive",
]

_EXPECTED_HUMAN_REQUIRED = [
    "Any DELETE, DROP, TRUNCATE, or data modification",
    "Any request affecting production user data",
    "Any irreversible state change (payment, account deletion)",
    "Any action outside test accounts",
    "Any Tier C action where SafeExploitHarness FAILED",
    "SSTI beyond expression evaluation (file read, RCE)",
    "Any shell command beyond OOB ping",
    "Any deserialization gadget execution beyond OOB",
    "Any financial transaction (even test accounts)",
]

_EXPECTED_VULN_TYPES = {
    "sqli", "ssti", "lfi", "path_traversal", "crlf_injection", "xxe",
    "cmd_injection", "deserialization", "xss", "csrf", "open_redirect",
    "prototype_pollution", "cors", "idor", "bac", "jwt", "oauth",
    "mass_assignment", "auth", "graphql", "api_versioning", "ssrf",
    "host_header", "race", "websocket", "http_smuggling",
    "hardcoded_credentials", "nuclei", "business_logic",
}


class TestTierCRulesVerbatim:
    """v6.4-004: 29 auto_allow entries / 29 unique scanners (WebSocket
    de-duplicated, Business Logic present); 9 human_required entries."""

    def test_matches_section_10_1_auto_allow_exactly(self):
        assert TIER_C_RULES["auto_allow"] == _EXPECTED_AUTO_ALLOW

    def test_matches_section_10_1_human_required_exactly(self):
        assert TIER_C_RULES["human_required"] == _EXPECTED_HUMAN_REQUIRED

    def test_auto_allow_has_exactly_29_entries(self):
        assert len(TIER_C_RULES["auto_allow"]) == 29

    def test_auto_allow_entries_are_all_unique(self):
        """v6.4-004's specific bug: WebSocket appeared twice. Pin uniqueness,
        not just count, so that regression can never silently re-occur."""
        assert len(set(TIER_C_RULES["auto_allow"])) == 29

    def test_websocket_appears_exactly_once(self):
        websocket_entries = [e for e in TIER_C_RULES["auto_allow"] if e.startswith("WebSocket")]
        assert len(websocket_entries) == 1

    def test_business_logic_is_present(self):
        """v6.4-004's other half: Business Logic was previously absent."""
        assert any(e.startswith("Business logic") for e in TIER_C_RULES["auto_allow"])

    def test_human_required_has_exactly_9_entries(self):
        assert len(TIER_C_RULES["human_required"]) == 9


class TestAutoAllowByVulnType:
    def test_has_exactly_29_keys(self):
        assert len(AUTO_ALLOW_BY_VULN_TYPE) == 29

    def test_keys_are_the_29_scanner_registry_keys(self):
        assert set(AUTO_ALLOW_BY_VULN_TYPE.keys()) == _EXPECTED_VULN_TYPES

    def test_is_a_bijection_onto_the_verbatim_list(self):
        """Every auto_allow entry is mapped to exactly one vuln_type, and
        every vuln_type maps to a real (not fabricated) auto_allow string."""
        mapped_values = list(AUTO_ALLOW_BY_VULN_TYPE.values())
        assert len(set(mapped_values)) == 29
        assert sorted(mapped_values) == sorted(TIER_C_RULES["auto_allow"])

    @pytest.mark.parametrize(
        "vuln_type,expected_substring",
        [
            ("xss", "XSS benign console.log probe"),
            ("sqli", "SQLi read-only UNION SELECT"),
            ("ssrf", "SSRF metadata read"),
            ("business_logic", "Business logic workflow-assumption"),
            ("websocket", "WebSocket auth-bypass"),
            ("nuclei", "Nuclei Tier A/B templates only"),
        ],
    )
    def test_spot_check_mappings(self, vuln_type, expected_substring):
        assert expected_substring in AUTO_ALLOW_BY_VULN_TYPE[vuln_type]


class TestAutonomousRiskGateDecide:
    def test_tier_a_always_autonomous_any_program_type(self):
        gate = AutonomousRiskGate()
        for program_type in ("bug_bounty", "vdp"):
            d = gate.decide("xss", TierLevel.TIER_A, program_type=program_type)
            assert d.autonomous is True

    def test_tier_b_always_autonomous_any_program_type(self):
        gate = AutonomousRiskGate()
        for program_type in ("bug_bounty", "vdp"):
            d = gate.decide("xss", TierLevel.TIER_B, program_type=program_type)
            assert d.autonomous is True

    def test_tier_d_never_autonomous_regardless_of_program_type(self):
        gate = AutonomousRiskGate()
        for program_type in ("bug_bounty", "vdp"):
            d = gate.decide("cmd_injection", TierLevel.TIER_D, program_type=program_type)
            assert d.autonomous is False
            assert "TIER_D" in d.reason

    def test_tier_c_autonomous_for_bug_bounty_when_on_auto_allow_list(self):
        gate = AutonomousRiskGate()
        d = gate.decide("xss", TierLevel.TIER_C, program_type="bug_bounty")
        assert d.autonomous is True
        assert d.reason == AUTO_ALLOW_BY_VULN_TYPE["xss"]

    def test_tier_c_blocked_for_vdp_even_when_on_auto_allow_list(self):
        gate = AutonomousRiskGate()
        d = gate.decide("xss", TierLevel.TIER_C, program_type="vdp")
        assert d.autonomous is False
        assert "VDP" in d.reason

    def test_tier_c_blocked_when_harness_failed(self):
        gate = AutonomousRiskGate()
        d = gate.decide("xss", TierLevel.TIER_C, program_type="bug_bounty", harness_failed=True)
        assert d.autonomous is False
        assert "SafeExploitHarness FAILED" in d.reason

    def test_tier_c_blocked_for_unknown_vuln_type(self):
        gate = AutonomousRiskGate()
        d = gate.decide("not_a_real_scanner", TierLevel.TIER_C, program_type="bug_bounty")
        assert d.autonomous is False

    def test_decide_never_raises_for_any_valid_tier(self):
        gate = AutonomousRiskGate()
        for tier in TierLevel:
            for program_type in ("bug_bounty", "vdp"):
                result = gate.decide("xss", tier, program_type=program_type)
                assert isinstance(result, RiskDecision)

    def test_reason_is_never_empty(self):
        gate = AutonomousRiskGate()
        for tier in TierLevel:
            d = gate.decide("xss", tier, program_type="bug_bounty")
            assert d.reason
