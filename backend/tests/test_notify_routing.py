"""notify() + the outbox routing layer.

notify() now just enqueues a Discord message tagged with its category; the
Discord-vs-push decision happens at the outbox (resolve_routing), so EVERY
message — notify() callers and direct enqueue_discord_message callers — flows
through one router.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_operator(monkeypatch):
    import notifications
    notifications._reset_operator_cache()
    notifications._reset_prefs_cache()
    monkeypatch.setattr(notifications, "_operator_user_id", lambda: "op1")


def test_notify_enqueues_with_explicit_category(monkeypatch):
    import notifications
    calls = []
    monkeypatch.setattr(
        notifications, "_discord_sink",
        lambda channel, content, embed, notif_key=None: calls.append((channel, content, embed, notif_key)),
    )
    notifications.notify(
        category="order_fill", instance_id="i1", title="Filled",
        body="ORDER FILL [i1] BUY 1 AAPL", discord_channel="trades",
        discord_embed={"title": "Filled: AAPL"},
    )
    assert calls == [("trades", "ORDER FILL [i1] BUY 1 AAPL", {"title": "Filled: AAPL"}, "order_fill")]


def _routing(monkeypatch, route, key="order_fill"):
    import notifications
    monkeypatch.setattr(notifications, "_operator_prefs", lambda conn: ("op1", {key: route}))
    return notifications


def test_resolve_routing_discord_only(monkeypatch):
    n = _routing(monkeypatch, {"discord": True, "push": False})
    out = n.resolve_routing(None, "trades", "ORDER FILL [main] BUY 1 AAPL")
    assert out["notif_key"] == "order_fill"
    assert out["discord"] is True and out["push"] is False
    assert "push_user_id" not in out


def test_resolve_routing_push_sets_fields(monkeypatch):
    n = _routing(monkeypatch, {"discord": True, "push": True})
    out = n.resolve_routing(None, "trades", "ORDER FILL [main] BUY 1 AAPL @ $100")
    assert out["push"] is True
    assert out["push_user_id"] == "op1"
    assert "ORDER FILL" in out["push_body"]
    assert out["push_data"]["notif_key"] == "order_fill"


def test_resolve_routing_both_off(monkeypatch):
    n = _routing(monkeypatch, {"discord": False, "push": False})
    out = n.resolve_routing(None, "trades", "ORDER FILL [main]")
    assert out["discord"] is False and out["push"] is False


def test_resolve_routing_explicit_key_overrides_content(monkeypatch):
    import notifications
    monkeypatch.setattr(notifications, "_operator_prefs",
                        lambda conn: ("op1", {"halt": {"discord": True, "push": True}}))
    # content looks like a fill, but the explicit notif_key wins
    out = notifications.resolve_routing(None, "notifications", "ORDER FILL [x]", notif_key="halt")
    assert out["notif_key"] == "halt" and out["push"] is True


def test_resolve_routing_classifies_direct_message(monkeypatch):
    # a direct enqueue (no explicit key) — e.g. broker boot — is classified
    n = _routing(monkeypatch, {"discord": True, "push": True}, key="broker_boot")
    out = n.resolve_routing(None, "notifications", "[alpaca-main] Broker boot | broker=alpaca")
    assert out["notif_key"] == "broker_boot" and out["push"] is True


def test_resolve_routing_fail_open(monkeypatch):
    import notifications
    def _boom(conn):
        raise RuntimeError("rethink down")
    monkeypatch.setattr(notifications, "_operator_prefs", _boom)
    out = notifications.resolve_routing(None, "trades", "ORDER FILL [main]")
    # a routing failure must NEVER silence the real-money Discord path
    assert out["discord"] is True and out["push"] is False


def test_resolve_routing_unknown_type_defaults_discord(monkeypatch):
    n = _routing(monkeypatch, {"discord": True, "push": False}, key="order_fill")
    # an unclassifiable message -> 'other' (not in the stubbed prefs) -> default discord on
    out = n.resolve_routing(None, "cli", "some random message")
    assert out["notif_key"] == "other"
    assert out["discord"] is True and out["push"] is False
