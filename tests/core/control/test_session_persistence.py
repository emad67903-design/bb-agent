"""
Implements: Section 6.3 / Section 8.2 test coverage --
core/control/session_persistence.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.control.session_persistence import (
    CheckpointStore,
    CheckpointStoreUnavailable,
    checkpoint_after_phase2,
)
from core.ontology.mental_model import MentalModel


class _FakeCheckpointStore:
    """Test double satisfying `CheckpointStore` -- the real PostgreSQL-backed
    implementation doesn't exist yet (module docstring), same pattern as
    `tests/core/governance/test_safety_gate.py`'s `_FakeApprovalManager`
    for `ApprovalManagerProtocol`."""

    def __init__(self) -> None:
        self.saved_checkpoints: list[dict] = []

    def save_checkpoint(
        self, *, session_id: str, phase: str, mental_model: MentalModel, checkpointed_at: datetime
    ) -> None:
        self.saved_checkpoints.append(
            {
                "session_id": session_id,
                "phase": phase,
                "mental_model": mental_model,
                "checkpointed_at": checkpointed_at,
            }
        )


def _sample_mental_model(is_partial: bool = False) -> MentalModel:
    return MentalModel(
        business_purpose="Sells widgets online",
        roles=["anonymous visitor", "customer"],
        trust_boundaries=["anonymous -> customer"],
        data_flows=["checkout form -> payment gateway"],
        assumptions=[],
        is_partial=is_partial,
    )


class TestCheckpointStoreProtocol:
    def test_fake_store_satisfies_protocol_structurally(self):
        store: CheckpointStore = _FakeCheckpointStore()
        assert hasattr(store, "save_checkpoint")


class TestCheckpointAfterPhase2:
    def test_saves_via_store_with_correct_fields(self):
        store = _FakeCheckpointStore()
        mm = _sample_mental_model()
        now = datetime(2026, 8, 2, tzinfo=timezone.utc)

        checkpoint_after_phase2(session_id="sess-1", mental_model=mm, store=store, now=now)

        assert len(store.saved_checkpoints) == 1
        saved = store.saved_checkpoints[0]
        assert saved["session_id"] == "sess-1"
        assert saved["phase"] == "mental_model"
        assert saved["mental_model"] is mm  # identity, not just equality
        assert saved["checkpointed_at"] == now

    def test_checkpoints_partial_mental_model_too(self):
        """Section 6.3 doesn't condition the checkpoint on completeness --
        only on Phase 2 having finished, abort included."""
        store = _FakeCheckpointStore()
        mm = _sample_mental_model(is_partial=True)

        checkpoint_after_phase2(
            session_id="sess-1", mental_model=mm, store=store,
            now=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

        assert store.saved_checkpoints[0]["mental_model"].is_partial is True

    def test_defaults_now_to_timezone_aware_current_time_when_not_supplied(self):
        store = _FakeCheckpointStore()
        before = datetime.now(timezone.utc)

        checkpoint_after_phase2(session_id="sess-1", mental_model=_sample_mental_model(), store=store)

        after = datetime.now(timezone.utc)
        saved_at = store.saved_checkpoints[0]["checkpointed_at"]
        assert saved_at.tzinfo is not None
        assert before <= saved_at <= after

    def test_none_store_raises_checkpoint_store_unavailable(self):
        with pytest.raises(CheckpointStoreUnavailable):
            checkpoint_after_phase2(session_id="sess-1", mental_model=_sample_mental_model(), store=None)

    def test_none_store_logs_warning_before_raising(self, caplog):
        with caplog.at_level("WARNING", logger="core.control.session_persistence"):
            with pytest.raises(CheckpointStoreUnavailable):
                checkpoint_after_phase2(session_id="sess-42", mental_model=_sample_mental_model(), store=None)
        assert any(
            "CHECKPOINT_UNAVAILABLE" in record.message and "sess-42" in record.message
            for record in caplog.records
        )

    def test_none_store_does_not_call_anything_on_a_store(self):
        """Fails closed before touching any store -- there is nothing to
        assert "was not called" on when store is None itself, so this
        pins the actual failure mode: an exception, not a silent no-op
        return."""
        with pytest.raises(CheckpointStoreUnavailable):
            checkpoint_after_phase2(session_id="sess-1", mental_model=_sample_mental_model(), store=None)

    def test_multiple_checkpoints_accumulate_in_store(self):
        store = _FakeCheckpointStore()
        checkpoint_after_phase2(session_id="sess-1", mental_model=_sample_mental_model(), store=store,
                                 now=datetime(2026, 8, 2, tzinfo=timezone.utc))
        checkpoint_after_phase2(session_id="sess-2", mental_model=_sample_mental_model(), store=store,
                                 now=datetime(2026, 8, 2, 1, tzinfo=timezone.utc))
        assert len(store.saved_checkpoints) == 2
        assert {c["session_id"] for c in store.saved_checkpoints} == {"sess-1", "sess-2"}
