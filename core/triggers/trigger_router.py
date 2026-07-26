"""
Implements: Section 1 Layer 1 ("`TriggerRouter` -> Event, Schedule,
Condition, Webhook, Chat. All converge to `IntentEngine`."), Section
8.1's integration-pattern line ("TriggerLayer -> IntentEngine:
MissionGraph"), Section 12's Week 2 row title ("PersonaRouter + Risk
Gate + TriggerRouter").
Blueprint: bb_agent_v6.6_final_blueprint.md

SCOPE NOTE -- flagged, not silently decided (docs/DECISIONS.md, Week 2
section): Week 2's own itemized build-order description (Section 12,
the line directly beneath the Week 2 row's title) names
`webhook_trigger.py` as the trigger-layer deliverable and does not
mention `trigger_router.py` anywhere in its text -- grep-verified, zero
hits for "Trigger" in that description line. Only the row's TITLE says
"...+ TriggerRouter". This module is built on the strength of that
title and Section 1 Layer 1's architecture (a router that all five
trigger sources converge through is a real, separate component from any
one source), not on the itemized description, which is silent on it
either way. See docs/DECISIONS.md for why this was built anyway rather
than held back.

WEEK 2 SCOPE:

`TriggerRouter` is a pure dispatcher: it accepts a `TriggerEvent` from
any source and forwards it to a registered intent handler. It does NOT
construct, own, or manage the lifecycle of any of the five trigger
sources themselves (`event_trigger.py`, `schedule_trigger.py`,
`condition_trigger.py`, `core.triggers.webhook_trigger.WebhookTrigger`,
`chat_trigger.py`) -- only `webhook_trigger.py` exists this week (also
Week 2); the other four are unbuilt, and this module does not import or
stub any of them (same "don't invent the missing piece" discipline as
`core/triggers/webhook_trigger.py`'s deferral of `TriggerRouter` itself,
now on the other side of that same seam).

`IntentEngine` (Section 1 Layer 1's convergence target) does not exist
and has no explicit Section 12 week assignment anywhere -- grep-verified
against Section 12's build-order table (lines 1915-1944 of the
blueprint file): zero hits. Same gap class as docs/DECISIONS.md item
4's `BudgetProfile` and item 9's `token_throttler.py` week-gaps. Per
that established resolution pattern (identify the real first consumer,
land the type there when it arrives -- do not stub a fake one now),
`TriggerRouter.set_intent_handler()` exposes a plain callback hook
rather than importing or duck-typing an `IntentEngine` interface that
isn't specified anywhere yet.

`TriggerSource` / `TriggerEvent` (below) are this module's own types,
not added to `core/ontology/enums.py` -- same documented, single-file,
low-cross-component-blast-radius judgment already applied to
`webhook_trigger.py`'s `WebhookEvent` and `persona_router.py`'s
`PersonaName` this same week. If/when the other four trigger sources
are built and need a genuinely shared event shape, that shared type
likely belongs in `core/ontology/` at that point.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from core.triggers.webhook_trigger import WebhookEvent

logger = logging.getLogger(__name__)


class TriggerSource(str, Enum):
    """The five convergent trigger sources, Section 1 Layer 1, spelled
    exactly as the blueprint names them."""

    EVENT = "event"
    SCHEDULE = "schedule"
    CONDITION = "condition"
    WEBHOOK = "webhook"
    CHAT = "chat"


@dataclass(frozen=True)
class TriggerEvent:
    """One dispatched trigger, source-agnostic. All five sources produce
    this same shape so `TriggerRouter` (and eventually `IntentEngine`)
    handle one contract rather than five.

    Attributes:
        source: Which of the five sources produced this event.
        payload: Source-specific data (e.g. a webhook's path/body, a
            schedule's fire time). Deliberately a loose `dict` rather
            than a per-source dataclass hierarchy -- only `webhook`'s
            shape (`WebhookEvent`) exists this week; inventing dataclass
            fields for four sources that don't exist yet would be
            guessing at their eventual shape.
        received_at: Unix timestamp of dispatch.
    """

    source: TriggerSource
    payload: dict[str, Any]
    received_at: float = field(default_factory=time.time)


def from_webhook_event(event: WebhookEvent) -> TriggerEvent:
    """Adapts the one concrete trigger source that exists this week
    (`core.triggers.webhook_trigger.WebhookEvent`) into the router's
    source-agnostic `TriggerEvent` shape.

    Args:
        event: A received webhook call.

    Returns:
        The equivalent TriggerEvent, source=TriggerSource.WEBHOOK.
    """
    return TriggerEvent(
        source=TriggerSource.WEBHOOK,
        payload={"path": event.path, "method": event.method, "headers": event.headers, "body": event.body},
    )


class TriggerRouter:
    """Section 1 Layer 1: the convergence point all five trigger sources
    dispatch through on their way to IntentEngine (not yet built -- see
    module docstring). Pure dispatch: does not construct or manage any
    trigger source's lifecycle.
    """

    def __init__(self) -> None:
        self._intent_handler: Callable[[TriggerEvent], None] | None = None
        self._dispatch_count_by_source: dict[TriggerSource, int] = {source: 0 for source in TriggerSource}

    def set_intent_handler(self, handler: Callable[[TriggerEvent], None] | None) -> None:
        """Registers the callback that receives every dispatched
        TriggerEvent -- IntentEngine's eventual role, once it exists.

        Args:
            handler: Called synchronously from `dispatch()` for every
                event. `None` clears the handler (dispatch() then only
                counts and logs, per `dispatch()`'s own docstring).
        """
        self._intent_handler = handler

    def dispatch(self, event: TriggerEvent) -> None:
        """Forwards `event` to the registered intent handler, if any.

        Args:
            event: The TriggerEvent to dispatch (e.g. from
                `from_webhook_event()`).

        Note:
            If no handler is registered (IntentEngine not built yet),
            this still records the dispatch count per source and logs
            it -- so nothing is silently dropped without a trace even
            before IntentEngine exists to actually act on it.
        """
        self._dispatch_count_by_source[event.source] += 1
        if self._intent_handler is not None:
            self._intent_handler(event)
        else:
            logger.info(
                "[TRIGGER_DISPATCHED_NO_HANDLER] source=%s (IntentEngine not yet wired in)", event.source.value
            )

    def dispatch_count(self, source: TriggerSource) -> int:
        """Number of events dispatched from `source` so far this router's lifetime."""
        return self._dispatch_count_by_source[source]

    @property
    def has_intent_handler(self) -> bool:
        return self._intent_handler is not None
