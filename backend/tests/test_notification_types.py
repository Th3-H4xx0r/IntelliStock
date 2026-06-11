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


def test_default_routing_covers_all_keys_discord_on_push_off():
    from notification_types import default_routing, NOTIFICATION_TYPE_KEYS
    r = default_routing()
    assert set(r.keys()) == set(NOTIFICATION_TYPE_KEYS)
    for v in r.values():
        assert v["discord"] is True and v["push"] is False


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
