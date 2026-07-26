"""
Implements: Section 3 -- core/governance/safety_gate.py ("Tier A/B/C/D
enforcement" / "VDP enforcement (R-L5 fix)").
Also implements: Section 10.1 (Permanent Tier Boundaries, VDP
enforcement code block, TIER_D's "NEVER autonomous" rule), Section 10.6
(Tier D Human Approval Flow -- interface only, see scope note below).
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 2 SCOPE -- two responsibilities, per Section 10.1:

(a) VDP tier cap (`enforce_vdp_tier_cap`): scope.yaml's `program_type`
    caps VDP targets at TIER_B. Any TIER_C or TIER_D request on a VDP
    target is rejected outright, logged `[VDP_TIER_CAP]`, and (if a
    notifier is supplied) reported for a human to see. This is Section
    10.1's own code block, transcribed with the message text it gives
    (see the message-text arbitration note below).

(b) TIER_D routing (`route_tier_d_action`): every TIER_D request,
    regardless of program_type, must route to Section 10.6's human
    approval workflow -- "NEVER autonomous. Human approval required" is
    permanent and unconditional, not VDP-specific (see
    core/governance/autonomous_risk_gate.py's own docstring for why that
    module does NOT duplicate this: TIER_D handling belongs here).

NOT built here: Section 10.6's actual approval WORKFLOW --
checkpointing, the real Telegram `/approve`/`/deny` round-trip, the
30-minute timeout, `approval_manager.py` and `telegram_bot.py`
themselves. Neither file exists yet, and -- same gap shape as
`IntentEngine` (docs/DECISIONS.md, Week 2 section) -- neither has an
explicit Section 12 week assignment anywhere. `route_tier_d_action`
below defines the MINIMAL interface (`ApprovalManagerProtocol`) that
whichever future `approval_manager.py` implements, and fails closed
(raises `ApprovalManagerUnavailable`) rather than silently allowing a
TIER_D action through when no real approval manager is wired in --
exactly because "TIER_D never autonomous" cannot be satisfied by simply
not calling this function.

Message-text arbitration (Week 2 continuation prompt, explicit
instruction, recorded here and in docs/DECISIONS.md): Section 3's tree
comment and Section 10.1's code block give two slightly different
VDP_BLOCKED message strings. Section 10.1's code block is used verbatim
below, per the continuation prompt's own instruction that code blocks
are authoritative over abbreviated tree-comment pointers throughout
this document.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from core.ontology.enums import TierLevel

logger = logging.getLogger(__name__)


class TierCapExceeded(Exception):
    """Raised by `enforce_vdp_tier_cap` when a requested tier exceeds
    TIER_B on a VDP-program target (Section 10.1)."""


class ApprovalManagerUnavailable(Exception):
    """Raised by `route_tier_d_action` when no `approval_manager` was
    supplied. Section 10.6's real approval_manager.py does not exist yet
    (module docstring) -- this is the fail-closed behavior in its
    absence, not a silent pass-through that would let a TIER_D action
    proceed autonomously by default."""


class ApprovalManagerProtocol(Protocol):
    """Minimal interface Section 10.6's `approval_manager.py` (not yet
    built) must satisfy for `route_tier_d_action` to hand off a TIER_D
    request to it. Deliberately small: this Week 2 module only needs to
    know that submitting a request returns an identifier immediately
    (non-blocking) -- the actual human round-trip (Telegram send, 30-min
    timeout, `/approve`/`/deny`, Section 10.6) happens asynchronously
    inside whatever concretely implements this protocol later.
    """

    def submit_for_approval(self, *, vuln_type: str, session_id: str, action_description: str) -> str:
        """Submits a TIER_D action for human approval.

        Args:
            vuln_type: Scanner-registry key the action concerns.
            session_id: The current session's identifier.
            action_description: Human-readable description of the
                proposed action (Section 10.6: shown to the human
                alongside the risk level and /approve or /deny options).

        Returns:
            An approval-request identifier (Section 10.6's `{id}` in
            "/approve {id}" / "/deny {id}").
        """
        ...


def enforce_vdp_tier_cap(
    requested_tier: TierLevel,
    program_type: str,
    *,
    notifier: Callable[[str], None] | None = None,
) -> None:
    """Section 10.1's VDP tier cap, verbatim: `if scope.program_type ==
    "vdp": if requested_tier > TierLevel.TIER_B: ... raise TierCapExceeded`.

    Args:
        requested_tier: The tier being requested for some action.
        program_type: "bug_bounty" or "vdp" (see
            core/governance/scope_config_generator.py's
            `load_program_type`, which a caller uses to obtain this from
            configs/scope.yaml -- this function takes the already-loaded
            value rather than reading the file itself, so it stays
            testable without a real scope.yaml on disk, matching the
            Engineering Constitution's "explicit parameters over runtime
            introspection" principle applied to file I/O as much as to
            call-stack inspection).
        notifier: Called with the VDP_BLOCKED message on violation (e.g.
            a future telegram_bot.py's send method). `None` (default)
            skips notification but still logs and raises -- telegram_bot.py
            does not exist yet (module docstring).

    Raises:
        TierCapExceeded: If `program_type == "vdp"` and `requested_tier`
            exceeds `TierLevel.TIER_B`.
    """
    if program_type == "vdp" and requested_tier > TierLevel.TIER_B:
        logger.warning("[VDP_TIER_CAP] requested_tier=%s program_type=vdp", requested_tier.name)
        message = "VDP_BLOCKED: Tier C/D action attempted on VDP target"
        if notifier is not None:
            notifier(message)
        raise TierCapExceeded("VDP: max TIER_B; action blocked")


def route_tier_d_action(
    requested_tier: TierLevel,
    *,
    vuln_type: str,
    session_id: str,
    action_description: str,
    approval_manager: ApprovalManagerProtocol | None,
) -> str:
    """Section 10.1: "TIER_D (destructive): NEVER autonomous. Human
    approval required." -- permanent and unconditional, independent of
    program_type (contrast `enforce_vdp_tier_cap`, which is VDP-specific).

    Args:
        requested_tier: Must be `TierLevel.TIER_D` -- see Raises below.
        vuln_type: Scanner-registry key the action concerns.
        session_id: The current session's identifier.
        action_description: Human-readable description of the proposed
            action.
        approval_manager: An `ApprovalManagerProtocol` implementation.
            No default of `None` silently accepted as "proceed" -- see
            `ApprovalManagerUnavailable` below.

    Returns:
        The approval-request identifier returned by
        `approval_manager.submit_for_approval(...)`.

    Raises:
        ValueError: If `requested_tier` is not `TierLevel.TIER_D` --
            this function is specifically the TIER_D route; a caller
            passing any other tier is a programming error, not a
            security decision this function should silently absorb.
        ApprovalManagerUnavailable: If `approval_manager` is `None` --
            fail-closed, since "TIER_D never autonomous" cannot be
            satisfied by simply skipping the routing step.
    """
    if requested_tier is not TierLevel.TIER_D:
        raise ValueError(f"route_tier_d_action is for TierLevel.TIER_D only, got {requested_tier.name}")

    if approval_manager is None:
        logger.warning("[TIER_D_NO_APPROVAL_MANAGER] vuln_type=%s session_id=%s", vuln_type, session_id)
        raise ApprovalManagerUnavailable(
            "TIER_D action requires human approval, but no approval_manager is configured "
            "(Section 10.6's approval_manager.py does not exist yet -- see module docstring); "
            "refusing to proceed rather than allowing this through by default"
        )

    logger.info("[TIER_D_ROUTED_TO_APPROVAL] vuln_type=%s session_id=%s", vuln_type, session_id)
    return approval_manager.submit_for_approval(
        vuln_type=vuln_type, session_id=session_id, action_description=action_description
    )
