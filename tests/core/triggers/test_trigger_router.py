"""
Implements: Section 1 Layer 1 / Section 8.1 test coverage --
core/triggers/trigger_router.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import http.client
import socket
import time

import pytest

from core.triggers.trigger_router import TriggerEvent, TriggerRouter, TriggerSource, from_webhook_event
from core.triggers.webhook_trigger import WebhookEvent, WebhookTrigger


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestTriggerSource:
    def test_has_exactly_five_members(self):
        assert len(TriggerSource) == 5

    def test_values_match_section_1_layer_1(self):
        assert {s.value for s in TriggerSource} == {"event", "schedule", "condition", "webhook", "chat"}


class TestFromWebhookEvent:
    def test_adapts_webhook_event_to_trigger_event(self):
        webhook_event = WebhookEvent(path="/x", method="POST", headers={"a": "b"}, body=b"payload")
        trigger_event = from_webhook_event(webhook_event)
        assert trigger_event.source == TriggerSource.WEBHOOK
        assert trigger_event.payload["path"] == "/x"
        assert trigger_event.payload["method"] == "POST"
        assert trigger_event.payload["headers"] == {"a": "b"}
        assert trigger_event.payload["body"] == b"payload"


class TestTriggerRouterDispatch:
    def test_no_handler_by_default(self):
        router = TriggerRouter()
        assert router.has_intent_handler is False

    def test_dispatch_without_handler_does_not_raise(self):
        router = TriggerRouter()
        event = TriggerEvent(source=TriggerSource.WEBHOOK, payload={})
        router.dispatch(event)  # must not raise even with IntentEngine unwired

    def test_dispatch_calls_registered_handler(self):
        router = TriggerRouter()
        received: list[TriggerEvent] = []
        router.set_intent_handler(received.append)
        event = TriggerEvent(source=TriggerSource.CHAT, payload={"msg": "hi"})
        router.dispatch(event)
        assert received == [event]

    def test_has_intent_handler_true_after_set(self):
        router = TriggerRouter()
        router.set_intent_handler(lambda e: None)
        assert router.has_intent_handler is True

    def test_set_intent_handler_none_clears_it(self):
        router = TriggerRouter()
        router.set_intent_handler(lambda e: None)
        router.set_intent_handler(None)
        assert router.has_intent_handler is False

    def test_dispatch_count_increments_per_source(self):
        router = TriggerRouter()
        router.dispatch(TriggerEvent(source=TriggerSource.WEBHOOK, payload={}))
        router.dispatch(TriggerEvent(source=TriggerSource.WEBHOOK, payload={}))
        router.dispatch(TriggerEvent(source=TriggerSource.CHAT, payload={}))
        assert router.dispatch_count(TriggerSource.WEBHOOK) == 2
        assert router.dispatch_count(TriggerSource.CHAT) == 1
        assert router.dispatch_count(TriggerSource.EVENT) == 0

    def test_dispatch_still_counts_when_no_handler_registered(self):
        router = TriggerRouter()
        router.dispatch(TriggerEvent(source=TriggerSource.SCHEDULE, payload={}))
        assert router.dispatch_count(TriggerSource.SCHEDULE) == 1


class TestWebhookToRouterIntegration:
    """Real end-to-end path: an actual socket-level webhook POST, through
    WebhookTrigger's background thread, adapted, and dispatched into
    TriggerRouter -- not just each piece mocked in isolation."""

    def test_real_webhook_post_reaches_router_via_adapter(self):
        router = TriggerRouter()
        received: list[TriggerEvent] = []
        router.set_intent_handler(received.append)

        trigger = WebhookTrigger(port=_free_port())
        trigger.set_on_event(lambda webhook_event: router.dispatch(from_webhook_event(webhook_event)))
        trigger.start()
        try:
            time.sleep(0.05)
            conn = http.client.HTTPConnection("127.0.0.1", trigger.port, timeout=2)
            conn.request("POST", "/webhook/test", body=b'{"x": 1}')
            conn.getresponse().read()
            conn.close()
        finally:
            trigger.stop()

        assert len(received) == 1
        assert received[0].source == TriggerSource.WEBHOOK
        assert received[0].payload["path"] == "/webhook/test"
        assert received[0].payload["body"] == b'{"x": 1}'
        assert router.dispatch_count(TriggerSource.WEBHOOK) == 1
