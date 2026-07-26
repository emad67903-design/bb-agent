"""
Implements: Section 3 -- core/governance/autonomous_risk_gate.py
  ("Tier C explicit rules; full 29-scanner auto_allow")
Also implements: Section 2 Layer 8 ("AutonomousRiskGate -- rules-based
Tier C/D decisions; program_type (bug_bounty/vdp) enforced"), Section
10.1 (TIER_C_RULES, Permanent Tier Boundaries), v6.4-004 fix (auto_allow
corrected from 29 entries / 28 unique scanners -- WebSocket duplicated,
Business Logic absent -- to 29 entries / 29 unique scanners).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 2 SCOPE:

`TIER_C_RULES` (module-level dict, both `auto_allow` and
`human_required` lists) is transcribed VERBATIM from Section 10.1 --
not re-derived, not reworded, not reordered. `AUTO_ALLOW_BY_VULN_TYPE`
is an ADDITIONAL mapping this module builds on top of that verbatim
data: each of the 29 `auto_allow` entries names its vuln class in its
own text (e.g. "SQLi read-only UNION SELECT probe..." -> sqli), so each
was matched to its Section 3.1 scanner-registry key. This mapping is
NOT blueprint text -- it is a documented, mechanical transcription
choice (single file, no cross-component ontology type involved), unlike
the TierLevel gap this same week, which required an explicit stop
because two separate Week 2 components depended on an undeclared
ontology type. See docs/DECISIONS.md, Week 2 section, for the count
verification (29 entries, 29 unique keys, no duplicates) run against
the actual blueprint file rather than eyeballed.

`AutonomousRiskGate.decide()` is this module's own interpretive
addition, not blueprint pseudocode: Section 2 Layer 8 describes the
component ("rules-based Tier C/D decisions; program_type enforced")
without giving a method signature. It deliberately does NOT re-implement
Section 10.1's VDP tier-cap RAISE -- that hard gate is
`core/governance/safety_gate.py`'s `enforce_vdp_tier_cap()` (Section
10.1 attributes the VDP code block to safety_gate.py by name, twice).
This module's `decide()` returns a non-raising, informational
`RiskDecision` that separately reflects the same VDP fact for
logging/reporting purposes -- the two are complementary views of one
constraint, not duplicate enforcement; only safety_gate.py's function
actually blocks execution by raising.

Also NOT built here: Section 10.6's Tier D human-approval WORKFLOW
(`approval_manager.py`, `telegram_bot.py`) -- neither is a Week 2
deliverable (credential_lifecycle.py and webhook_trigger.py are this
week's control/trigger-layer components; approval_manager.py is not
named in any Section 12 row yet). `decide()` for TIER_D returns
autonomous=False; routing that to an actual Telegram approval
round-trip is later, unbuilt machinery.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.ontology.enums import TierLevel

# ---------------------------------------------------------------------
# Section 10.1 TIER_C_RULES -- VERBATIM. Do not reword, reorder, or
# "clean up" phrasing here; any future blueprint correction to this list
# must be applied by re-copying from Section 10.1, and any drift is a
# defect this list's own test (test_matches_section_10_1_auto_allow_exactly)
# is written to catch via a literal diff, not a count.
# ---------------------------------------------------------------------
TIER_C_RULES: dict[str, list[str]] = {
    "auto_allow": [
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
    ],
    "human_required": [
        "Any DELETE, DROP, TRUNCATE, or data modification",
        "Any request affecting production user data",
        "Any irreversible state change (payment, account deletion)",
        "Any action outside test accounts",
        "Any Tier C action where SafeExploitHarness FAILED",
        "SSTI beyond expression evaluation (file read, RCE)",
        "Any shell command beyond OOB ping",
        "Any deserialization gadget execution beyond OOB",
        "Any financial transaction (even test accounts)",
    ],
}

# ---------------------------------------------------------------------
# AUTO_ALLOW_BY_VULN_TYPE -- documented addition (see module docstring).
# Keys are the same 29 scanner-registry-key strings used throughout this
# codebase (core/ontology/findings.py's EvidenceChain.vuln_type, Section
# 3.1's scanner column) -- lower_snake_case, matching the 29 scanners
# named under core/scanners/ in Section 3's tree. Values are the exact
# (by identity, not copy) string from TIER_C_RULES["auto_allow"] above,
# so a lookup here can never drift from the verbatim list.
# ---------------------------------------------------------------------
_AUTO_ALLOW = TIER_C_RULES["auto_allow"]

AUTO_ALLOW_BY_VULN_TYPE: dict[str, str] = {
    "sqli": _AUTO_ALLOW[0],
    "ssti": _AUTO_ALLOW[1],
    "lfi": _AUTO_ALLOW[2],
    "path_traversal": _AUTO_ALLOW[3],
    "crlf_injection": _AUTO_ALLOW[4],
    "xxe": _AUTO_ALLOW[5],
    "cmd_injection": _AUTO_ALLOW[6],
    "deserialization": _AUTO_ALLOW[7],
    "xss": _AUTO_ALLOW[8],
    "csrf": _AUTO_ALLOW[9],
    "open_redirect": _AUTO_ALLOW[10],
    "prototype_pollution": _AUTO_ALLOW[11],
    "cors": _AUTO_ALLOW[12],
    "idor": _AUTO_ALLOW[13],
    "bac": _AUTO_ALLOW[14],
    "jwt": _AUTO_ALLOW[15],
    "oauth": _AUTO_ALLOW[16],
    "mass_assignment": _AUTO_ALLOW[17],
    "auth": _AUTO_ALLOW[18],
    "graphql": _AUTO_ALLOW[19],
    "api_versioning": _AUTO_ALLOW[20],
    "ssrf": _AUTO_ALLOW[21],
    "host_header": _AUTO_ALLOW[22],
    "race": _AUTO_ALLOW[23],
    "websocket": _AUTO_ALLOW[24],
    "http_smuggling": _AUTO_ALLOW[25],
    "hardcoded_credentials": _AUTO_ALLOW[26],
    "nuclei": _AUTO_ALLOW[27],
    "business_logic": _AUTO_ALLOW[28],
}


@dataclass(frozen=True)
class RiskDecision:
    """Non-raising output of `AutonomousRiskGate.decide()`.

    Attributes:
        vuln_type: The scanner-registry key this decision concerns.
        tier: The TierLevel that was requested.
        autonomous: True if this action may proceed without human
            approval, given tier, program_type, and harness state.
        reason: Human-readable justification -- either the matched
            TIER_C_RULES auto_allow string, or a plain-English citation
            of which rule blocked it. Never empty.
    """

    vuln_type: str
    tier: TierLevel
    autonomous: bool
    reason: str


class AutonomousRiskGate:
    """Section 2 Layer 8: rules-based Tier C/D decisions, program_type-aware.

    Does not raise. Callers that need a hard block on VDP targets use
    `core/governance/safety_gate.py`'s `enforce_vdp_tier_cap()` instead
    (see module docstring) -- this class is a decision oracle, not a
    gate that stops execution.
    """

    def decide(
        self,
        vuln_type: str,
        tier: TierLevel,
        *,
        program_type: str,
        harness_failed: bool = False,
    ) -> RiskDecision:
        """Decides whether `vuln_type` at `tier` may run autonomously.

        Args:
            vuln_type: Scanner-registry key (e.g. "xss", "sqli").
            tier: Requested TierLevel.
            program_type: "bug_bounty" or "vdp" (configs/scope.yaml).
            harness_failed: True if a SafeExploitHarness already ran for
                this candidate and failed (Section 10.1's
                human_required entry: "Any Tier C action where
                SafeExploitHarness FAILED"). Defaults False since Week 2
                has no concrete harnesses yet (Week 7) to have failed.

        Returns:
            A RiskDecision. Never raises -- an unknown vuln_type or an
            invalid tier still returns a RiskDecision with
            autonomous=False, fail-closed, rather than propagating an
            exception the caller may not expect from a decision oracle.
        """
        # TIER_A / TIER_B: "Always autonomous" (Section 10.1), unconditionally
        # -- program_type does not change this; only TIER_C's VDP cap and
        # TIER_D's permanent human-approval rule are program_type/tier
        # dependent.
        if tier in (TierLevel.TIER_A, TierLevel.TIER_B):
            return RiskDecision(vuln_type, tier, True, f"{tier.name}: always autonomous (Section 10.1)")

        # TIER_D: "NEVER autonomous. Human approval required." -- permanent,
        # regardless of program_type (Section 10.1's top-level boundary;
        # restated as a project-level core invariant). Not specific to VDP,
        # so this is NOT the [VDP_TIER_CAP] case -- no VDP-specific log tag
        # applies here.
        if tier is TierLevel.TIER_D:
            return RiskDecision(vuln_type, tier, False, "TIER_D destructive: NEVER autonomous (Section 10.1, permanent)")

        # tier is TIER_C from here on.
        if program_type == "vdp":
            # Same fact safety_gate.py's enforce_vdp_tier_cap() raises on;
            # surfaced here as a non-raising decision (module docstring).
            return RiskDecision(vuln_type, tier, False, "VDP programs cap at TIER_B (Section 10.1); TIER_C not permitted")

        if harness_failed:
            return RiskDecision(
                vuln_type, tier, False, "Any Tier C action where SafeExploitHarness FAILED (Section 10.1, human_required)"
            )

        auto_allow_reason = AUTO_ALLOW_BY_VULN_TYPE.get(vuln_type)
        if auto_allow_reason is None:
            return RiskDecision(vuln_type, tier, False, f"'{vuln_type}' has no TIER_C_RULES auto_allow entry")

        return RiskDecision(vuln_type, tier, True, auto_allow_reason)
