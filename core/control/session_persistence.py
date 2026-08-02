"""
Implements: Section 3 -- core/control/session_persistence.py ("Initial
checkpoint: after Phase 2 (MentalModel complete)"). Also implements
Section 6.3's "Initial checkpoint: Saved to PostgreSQL immediately after
Phase 2 completes, before any scanning begins," Section 6.11 item 4
("initial checkpoint after Phase 2; every 15 minutes thereafter"), and
Section 8.2's PostgreSQL row ("Checkpoints (initial: after Phase 2; then
every 15 min)").
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE, PRECISELY -- PHASE-2 TRIGGER ONLY, NOT THE 15-MINUTE PERIODIC
MECHANISM (docs/DECISIONS.md item 50): Section 3's own tree comment for
this file, and Section 6.11/8.2 independently, all name TWO claims: an
initial checkpoint after Phase 2, and a recurring checkpoint every 15
minutes "during active scanning" thereafter. This week's kickoff
instructions explicitly scope only the first: build only the Phase-2
initial-checkpoint trigger, and flag the periodic mechanism as a
separate, not-yet-assigned piece rather than building it under this
week's authorization by inference. The 15-minute mechanism needs a
running-session concept -- something that stays alive "during active
scanning" and fires on a timer -- that doesn't exist anywhere in this
codebase yet; not built here, not stubbed, left for whichever future
week actually owns a session loop.

WHAT "CHECKPOINT" ACTUALLY PERSISTS -- A CITED GAP, NOT INVENTED
(docs/DECISIONS.md item 50): no blueprint section anywhere gives a
checkpoint payload schema. The one concrete thing Section 6.3 says
exists at the trigger moment is the `MentalModel` itself: "Initial
checkpoint: Saved to PostgreSQL immediately after Phase 2 completes."
`ParentState` -- the type Section 3's `state.py` tree comment says
actually holds `mental_model` as one field among several siblings
(`ReconState`, `TestingState`, `ChainBudget`, `MemoryDecayPolicy`,
`context_window_snapshot`, `js_findings`) -- does not exist in this
repository yet (`core/mental_model/model.py`'s own docstring, Week 3,
already flagged this same absence for a different purpose). This module
therefore checkpoints `MentalModel` alone; checkpointing the rest of
`ParentState` is deferred to whenever `ParentState` itself is built.

STORAGE BACKEND -- SAME FAIL-CLOSED PROTOCOL PATTERN AS
`safety_gate.py`'s `ApprovalManagerProtocol` (Week 2), FOR THE SAME
REASON (docs/DECISIONS.md item 50): Section 8.2 names the destination
("PostgreSQL") but gives no schema, driver, or connection contract
anywhere, and this sandbox has no live `postgres_connection` capability
(docs/DECISIONS.md item 7: fails closed here by design, not oversight).
`CheckpointStore` below is the minimal interface a future
PostgreSQL-backed implementation must satisfy; `checkpoint_after_phase2`
raises `CheckpointStoreUnavailable` when none is supplied, rather than
silently skipping the checkpoint or writing to some ad hoc local file
Section 8.2 never authorized.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol

from core.ontology.mental_model import MentalModel

logger = logging.getLogger(__name__)


class CheckpointStoreUnavailable(Exception):
    """Raised by `checkpoint_after_phase2` when no `CheckpointStore` was supplied."""


class CheckpointStore(Protocol):
    """Minimal interface Section 8.2's PostgreSQL-backed checkpoint store
    (not yet built -- no schema or driver is specified anywhere in the
    blueprint) must satisfy for `checkpoint_after_phase2` to hand a
    completed-Phase-2 `MentalModel` off to it.
    """

    def save_checkpoint(
        self, *, session_id: str, phase: str, mental_model: MentalModel, checkpointed_at: datetime
    ) -> None:
        """Persists one checkpoint.

        Args:
            session_id: The current session's identifier.
            phase: Which phase this checkpoint was taken after. Always
                `"mental_model"` for this week's sole caller
                (`checkpoint_after_phase2`) -- included now, rather than
                hardcoded out of the interface entirely, because Section
                8.2 and Section 6.11 both describe recurring, not just
                initial, checkpoints, and a future periodic
                implementation of this same Protocol will need to
                distinguish which phase/moment each save corresponds to.
            mental_model: The `MentalModel` to persist.
            checkpointed_at: When the checkpoint was taken.
        """
        ...


def checkpoint_after_phase2(
    *,
    session_id: str,
    mental_model: MentalModel,
    store: CheckpointStore | None,
    now: datetime | None = None,
) -> None:
    """Section 6.3 / Section 8.2's Phase-2 initial checkpoint trigger.

    Fires once, immediately after `MentalModelBuilder` produces a
    `MentalModel` (Section 6.3: "Saved to PostgreSQL immediately after
    Phase 2 completes, before any scanning begins") -- including a
    partial one (`mental_model.is_partial`), since Section 6.3 does not
    condition the checkpoint on completeness, only on Phase 2 having
    finished, whether that's a normal completion or the 8-page/90-second
    abort path.

    Args:
        session_id: The current session's identifier.
        mental_model: The just-completed Phase 2 output.
        store: A `CheckpointStore` implementation. `None` fails closed
            (see Raises) rather than silently skipping the checkpoint.
        now: Timestamp recorded as `checkpointed_at`. Defaults to
            `datetime.now(timezone.utc)`; overridable for deterministic
            tests.

    Raises:
        CheckpointStoreUnavailable: If `store` is `None`.
    """
    if store is None:
        logger.warning("[CHECKPOINT_UNAVAILABLE] session_id=%s phase=mental_model", session_id)
        raise CheckpointStoreUnavailable(
            f"No CheckpointStore supplied; cannot save Phase 2 checkpoint for session {session_id!r} "
            "(Section 8.2's PostgreSQL-backed store does not exist yet -- see module docstring); "
            "refusing to silently skip the checkpoint"
        )

    timestamp = now if now is not None else datetime.now(timezone.utc)
    store.save_checkpoint(
        session_id=session_id, phase="mental_model", mental_model=mental_model, checkpointed_at=timestamp
    )
    logger.info("[CHECKPOINT_SAVED] session_id=%s phase=mental_model", session_id)
