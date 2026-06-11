"""Real-money guard: routing live alerts through notify() must NOT change the
Discord behavior under default preferences. With default prefs (discord on,
push off), each alert_* must still produce exactly ONE _safe_enqueue call with
the same channel + content + embed as before the routing refactor.

Also pins the category + channel each alert_* maps to.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def default_prefs(monkeypatch):
    import notifications
    from interactive_utils import _default_notification_categories
    monkeypatch.setattr(
        notifications, "_load_prefs",
        lambda uid: {"categories": _default_notification_categories()},
    )


def _capture_enqueue(monkeypatch):
    import live_alerts
    captured = []
    monkeypatch.setattr(
        live_alerts, "_safe_enqueue",
        lambda channel, content, embed=None, notif_key=None: captured.append((channel, content, embed, notif_key)),
    )
    return captured


def test_order_fill_discord_parity(default_prefs, monkeypatch):
    import live_alerts
    captured = _capture_enqueue(monkeypatch)
    live_alerts.alert_order_fill(
        instance_id="i1", symbol="AAPL", side="buy",
        filled_qty=1.0, filled_avg_price=100.0,
        client_order_id="cid1", broker_order_id="oid1",
    )
    assert len(captured) == 1
    channel, content, embed, notif_key = captured[0]
    assert channel == "trades"  # _channel() default
    assert content == "ORDER FILL [i1] BUY 1.0000 AAPL @ $100.00 = $100.00"
    assert embed["title"] == "Filled: AAPL"
    assert embed["color"] == 0x2ECC71
    assert notif_key == "order_fill"


def test_halt_routes_to_notifications_channel(default_prefs, monkeypatch):
    import live_alerts
    captured = _capture_enqueue(monkeypatch)
    live_alerts.alert_halt(instance_id="i1", reason="manual stop")
    assert len(captured) == 1
    channel, content, embed, notif_key = captured[0]
    assert channel == "notifications"
    assert content == "HALT [i1] manual stop"
    assert notif_key == "halt"


def test_drawdown_halt_routes_to_trades_channel(default_prefs, monkeypatch):
    import live_alerts
    captured = _capture_enqueue(monkeypatch)
    live_alerts.alert_drawdown_halt(
        instance_id="i1", drawdown_pct=5.0, peak_value=1000.0, current_value=950.0,
    )
    assert len(captured) == 1
    channel, _content, _embed, notif_key = captured[0]
    assert channel == "trades"
    assert notif_key == "drawdown_halt"


@pytest.mark.parametrize("fn_name,kwargs,category,channel", [
    ("alert_order_submit",
     dict(instance_id="i", symbol="AAPL", side="buy", qty=1.0, notional=None,
          client_order_id="c", broker_order_id="b"),
     "order_submit", "trades"),
    ("alert_order_fill",
     dict(instance_id="i", symbol="AAPL", side="buy", filled_qty=1.0,
          filled_avg_price=100.0, client_order_id="c", broker_order_id="b"),
     "order_fill", "trades"),
    ("alert_order_reject",
     dict(instance_id="i", symbol="AAPL", side="buy", reason_class="x",
          reason_text="y", client_order_id="c"),
     "order_reject", "trades"),
    ("alert_order_retry",
     dict(instance_id="i", symbol="AAPL", side="buy", original_qty=1.0,
          retry_qty=1.0, reason_class="x", reason_text="y", client_order_id="c"),
     "order_retry", "trades"),
    ("alert_strategy_error",
     dict(instance_id="i", tag="t", message="m"),
     "strategy_error", "trades"),
    ("alert_halt",
     dict(instance_id="i", reason="r"),
     "halt", "notifications"),
    ("alert_drawdown_halt",
     dict(instance_id="i", drawdown_pct=5.0, peak_value=1000.0, current_value=950.0),
     "drawdown_halt", "trades"),
    ("alert_strategy_start",
     dict(instance_id="i", strategy_name="s", date_key="2026-06-10",
          symbols_count=3, held_count=1, equity=1000.0),
     "strategy_start", "trades"),
    ("alert_crash_loop",
     dict(instance_id="i", restarts=3, window_sec=60),
     "crash_loop", "notifications"),
])
def test_each_alert_maps_to_category_and_channel(monkeypatch, fn_name, kwargs, category, channel):
    import live_alerts
    calls = []
    monkeypatch.setattr(live_alerts, "notify", lambda **kw: calls.append(kw))
    getattr(live_alerts, fn_name)(**kwargs)
    assert len(calls) == 1
    assert calls[0]["category"] == category
    assert calls[0]["discord_channel"] == channel
    assert calls[0]["instance_id"] == "i"
