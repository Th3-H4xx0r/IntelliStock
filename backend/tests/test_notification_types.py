"""Notification taxonomy + classifier."""
from __future__ import annotations


def test_explicit_key_wins():
    from notification_types import classify
    # even if content looks like something else, the explicit key is honored
    assert classify(content="ORDER FILL [x]", notif_key="halt") == "halt"
    # unknown explicit key falls through to content classification
    assert classify(content="ORDER FILL [x]", notif_key="bogus") == "order_fill"


def test_classify_by_content_prefix():
    from notification_types import classify
    assert classify(content="ORDER FILL [main] BUY 1 AAPL") == "order_fill"
    assert classify(content="ORDER SUBMIT [main] ...") == "order_submit"
    assert classify(content="HALT [main] manual") == "halt"
    assert classify(content="CRASH LOOP [main] ...") == "crash_loop"
    assert classify(content="Claude Code rate limit hit") == "claude_rate_limit"


def test_classify_broker_boot_with_instance_prefix():
    # actual content is "[alpaca-main] Broker boot | broker=alpaca ..."
    from notification_types import classify
    assert classify(content="[alpaca-main] Broker boot | broker=alpaca paper=False") == "broker_boot"
    assert classify(content="[alpaca-main] Monitor cycle | ...") == "monitor_cycle"


def test_classify_by_embed_title():
    from notification_types import classify
    assert classify(content="", embed={"title": "Backtest Finished"}) == "backtest"
    assert classify(content="", embed={"title": "Morning Brief — 2026-06-11"}) == "brief_morning"


def test_unclassified_falls_back_to_other():
    from notification_types import classify
    assert classify(content="some random message", channel="cli") == "other"
    assert classify(content=None, embed=None) == "other"


def test_default_routing_discord_on_push_off_except_critical():
    # Discord defaults ON for every type; push defaults OFF for every type EXCEPT
    # the curated push-on-by-default set (instance_crash) — a real-money instance
    # going dark warrants a phone push out of the box.
    from notification_types import (
        default_routing, NOTIFICATION_TYPE_KEYS, _PUSH_ON_BY_DEFAULT,
    )
    r = default_routing()
    assert set(r.keys()) == set(NOTIFICATION_TYPE_KEYS)
    for key, v in r.items():
        assert v["discord"] is True
        assert v["push"] is (key in _PUSH_ON_BY_DEFAULT)
    # the only push-on-by-default key today
    assert _PUSH_ON_BY_DEFAULT == {"instance_crash"}
    assert r["instance_crash"]["push"] is True


def test_instance_crash_type_present_and_classifies():
    from notification_types import NOTIFICATION_TYPE_KEYS, classify, type_for_key
    assert "instance_crash" in NOTIFICATION_TYPE_KEYS
    meta = type_for_key("instance_crash")
    assert meta["group"] == "Risk & Halts"
    assert meta["channel"] == "notifications"
    assert classify(content="INSTANCE CRASH [alpaca-main] supervisor died") == "instance_crash"


def test_groups_exclude_other_and_are_ordered():
    from notification_types import groups_in_order
    groups = groups_in_order()
    assert groups[0] == "Trading"
    assert "Other" not in groups
    assert "Risk & Halts" in groups and "Broker Health" in groups


def test_nine_live_alert_keys_present():
    from notification_types import NOTIFICATION_TYPE_KEYS
    for k in ["order_submit", "order_fill", "order_reject", "order_retry",
              "strategy_start", "strategy_error", "halt", "drawdown_halt", "crash_loop"]:
        assert k in NOTIFICATION_TYPE_KEYS
