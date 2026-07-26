"""
Implements: Section 3 / Section 8.5 test coverage -- core/triggers/webhook_trigger.py
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

import http.client
import socket
import time

import pytest

from core.triggers.webhook_trigger import WebhookEvent, WebhookTrigger


def _free_port() -> int:
    """Binds :0 to let the OS assign a free port, then releases it --
    standard test idiom to avoid hardcoding a port that might collide
    with another test run or the real race/smuggling engines (18080/18081)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def trigger():
    t = WebhookTrigger(port=_free_port())
    yield t
    if t.is_running:
        t.stop()


class TestBindHostHardcoded:
    def test_bind_host_is_127_0_0_1(self, trigger):
        assert trigger.bind_host == "127.0.0.1"

    def test_bind_host_has_no_constructor_parameter(self):
        """There must be no way to pass a host to override 127.0.0.1 --
        Section 8.5: 'the bind call itself must not accept an override
        to 0.0.0.0' (Week 2 continuation prompt)."""
        import inspect

        sig = inspect.signature(WebhookTrigger.__init__)
        assert "host" not in sig.parameters
        assert "bind_host" not in sig.parameters

    def test_source_contains_127_0_0_1_literal(self):
        """Mirrors cli/main.py's actual webhook_binding preflight check
        logic (_webhook_binds_localhost_only) so a regression is caught
        here too, not only when the full preflight suite runs."""
        import core.triggers.webhook_trigger as mod

        text = open(mod.__file__, encoding="utf-8").read()
        assert '"127.0.0.1"' in text or "'127.0.0.1'" in text

    def test_source_contains_no_wildcard_bind_literal(self):
        wildcard = "0.0.0.0"
        import core.triggers.webhook_trigger as mod

        text = open(mod.__file__, encoding="utf-8").read()
        assert f'"{wildcard}"' not in text
        assert f"'{wildcard}'" not in text


class TestServerLifecycle:
    def test_not_running_before_start(self, trigger):
        assert trigger.is_running is False

    def test_running_after_start(self, trigger):
        trigger.start()
        assert trigger.is_running is True

    def test_not_running_after_stop(self, trigger):
        trigger.start()
        trigger.stop()
        assert trigger.is_running is False

    def test_start_twice_raises(self, trigger):
        trigger.start()
        with pytest.raises(RuntimeError):
            trigger.start()

    def test_actually_binds_127_0_0_1_and_refuses_other_interfaces_implicitly(self, trigger):
        """Real socket-level check, not just a source-text scan: connecting
        to 127.0.0.1:port must succeed once started."""
        trigger.start()
        time.sleep(0.05)  # let the background thread reach accept()
        conn = http.client.HTTPConnection("127.0.0.1", trigger.port, timeout=2)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        conn.close()


class TestWebhookDelivery:
    def test_post_is_forwarded_to_on_event_callback(self, trigger):
        received: list[WebhookEvent] = []
        trigger.set_on_event(received.append)
        trigger.start()
        time.sleep(0.05)

        conn = http.client.HTTPConnection("127.0.0.1", trigger.port, timeout=2)
        conn.request("POST", "/webhook/hackerone", body=b'{"finding": "test"}', headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()

        assert resp.status == 202
        assert len(received) == 1
        assert received[0].path == "/webhook/hackerone"
        assert received[0].method == "POST"
        assert received[0].body == b'{"finding": "test"}'
        assert received[0].headers.get("content-type") == "application/json"

    def test_post_with_no_on_event_registered_still_returns_202(self, trigger):
        """on_event is None until TriggerRouter exists (module docstring)
        -- must not crash the handler."""
        trigger.start()
        time.sleep(0.05)
        conn = http.client.HTTPConnection("127.0.0.1", trigger.port, timeout=2)
        conn.request("POST", "/whatever", body=b"x")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 202

    def test_oversized_body_rejected_with_413(self, trigger):
        import core.triggers.webhook_trigger as mod

        original_cap = mod._MAX_BODY_BYTES
        mod._MAX_BODY_BYTES = 10  # shrink for the test rather than sending 1 MiB
        try:
            trigger.start()
            time.sleep(0.05)
            conn = http.client.HTTPConnection("127.0.0.1", trigger.port, timeout=2)
            conn.request("POST", "/webhook", body=b"x" * 100)
            resp = conn.getresponse()
            resp.read()
            conn.close()
            assert resp.status == 413
        finally:
            mod._MAX_BODY_BYTES = original_cap

    def test_set_on_event_updates_a_running_server(self, trigger):
        received: list[WebhookEvent] = []
        trigger.start()
        time.sleep(0.05)
        trigger.set_on_event(received.append)

        conn = http.client.HTTPConnection("127.0.0.1", trigger.port, timeout=2)
        conn.request("POST", "/x", body=b"y")
        conn.getresponse().read()
        conn.close()

        assert len(received) == 1


class TestWebhookEvent:
    def test_is_frozen(self):
        event = WebhookEvent(path="/x", method="POST", headers={}, body=b"")
        with pytest.raises(AttributeError):
            event.path = "/y"  # type: ignore[misc]

    def test_body_defaults_to_empty_bytes(self):
        event = WebhookEvent(path="/x", method="GET", headers={})
        assert event.body == b""
