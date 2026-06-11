"""notify() routing layer — fans a category out to Discord and/or iOS push per
preference. Sinks are injectable module seams so we can assert routing without
a DB, Discord, or Apple.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_operator(monkeypatch):
    """Stop notify() from resolving the operator via a real DB connection."""
    import notifications
    notifications._reset_operator_cache()
    monkeypatch.setattr(notifications, "_operator_user_id", lambda: "op1")


def _patch(monkeypatch, route):
    import notifications
    calls = {"discord": [], "push": []}
    monkeypatch.setattr(notifications, "_load_prefs",
                        lambda uid: {"categories": {"order_fill": route}})
    monkeypatch.setattr(notifications, "_discord_sink",
                        lambda channel, content, embed: calls["discord"].append((channel, content, embed)))
    monkeypatch.setattr(notifications, "_push_sink",
                        lambda uid, **kw: calls["push"].append((uid, kw)))
    return notifications, calls


def _fire(notifications):
    notifications.notify(
        category="order_fill", instance_id="inst1",
        title="Filled AAPL", body="ORDER FILL ... AAPL",
        discord_channel="trades", discord_embed={"title": "Filled: AAPL"},
        push_body="BUY 1 AAPL @ $100",
    )


def test_default_routes_discord_only(monkeypatch):
    notifications, calls = _patch(monkeypatch, {"discord": True, "push": False})
    _fire(notifications)
    assert calls["discord"] == [("trades", "ORDER FILL ... AAPL", {"title": "Filled: AAPL"})]
    assert calls["push"] == []


def test_push_only(monkeypatch):
    notifications, calls = _patch(monkeypatch, {"discord": False, "push": True})
    _fire(notifications)
    assert calls["discord"] == []
    assert len(calls["push"]) == 1
    uid, kw = calls["push"][0]
    assert kw["category"] == "order_fill"
    assert kw["body"] == "BUY 1 AAPL @ $100"


def test_both(monkeypatch):
    notifications, calls = _patch(monkeypatch, {"discord": True, "push": True})
    _fire(notifications)
    assert len(calls["discord"]) == 1
    assert len(calls["push"]) == 1


def test_neither(monkeypatch):
    notifications, calls = _patch(monkeypatch, {"discord": False, "push": False})
    _fire(notifications)
    assert calls["discord"] == [] and calls["push"] == []


def test_discord_sink_failure_does_not_block_push(monkeypatch):
    import notifications
    calls = {"push": []}
    monkeypatch.setattr(notifications, "_load_prefs",
                        lambda uid: {"categories": {"order_fill": {"discord": True, "push": True}}})
    def _boom(channel, content, embed):
        raise RuntimeError("discord down")
    monkeypatch.setattr(notifications, "_discord_sink", _boom)
    monkeypatch.setattr(notifications, "_push_sink",
                        lambda uid, **kw: calls["push"].append(kw))
    # must not raise, and push must still fire
    _fire(notifications)
    assert len(calls["push"]) == 1


def test_notify_uses_resolved_operator_for_prefs_and_push(monkeypatch):
    """The live-alert path passes no user_id; prefs AND push must use the SAME
    resolved operator id (not the literal default 'operator')."""
    import notifications
    monkeypatch.setattr(notifications, "_operator_user_id", lambda: "real-user-7")
    seen = {}
    def _prefs(uid):
        seen["prefs_uid"] = uid
        return {"categories": {"order_fill": {"discord": False, "push": True}}}
    def _push(uid, **kw):
        seen["push_uid"] = uid
    monkeypatch.setattr(notifications, "_load_prefs", _prefs)
    monkeypatch.setattr(notifications, "_push_sink", _push)
    monkeypatch.setattr(notifications, "_discord_sink", lambda *a, **k: None)
    _fire(notifications)  # user_id=None
    assert seen["prefs_uid"] == "real-user-7"
    assert seen["push_uid"] == "real-user-7"


def test_prefs_load_failure_falls_back_to_discord(monkeypatch):
    import notifications
    calls = {"discord": [], "push": []}
    def _boom(uid):
        raise RuntimeError("rethink down")
    monkeypatch.setattr(notifications, "_load_prefs", _boom)
    monkeypatch.setattr(notifications, "_discord_sink",
                        lambda channel, content, embed: calls["discord"].append((channel, content)))
    monkeypatch.setattr(notifications, "_push_sink",
                        lambda uid, **kw: calls["push"].append(kw))
    _fire(notifications)
    # fail-open to Discord preserves today's behavior when prefs are unreadable
    assert len(calls["discord"]) == 1
    assert calls["push"] == []
