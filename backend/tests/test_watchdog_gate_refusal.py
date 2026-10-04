"""A gate refusal on the watchdog says what it is, and alerts once (2026-10-02).

From 2026-10-01 05:48:52 UTC the swing-paper watchdog was silent. Every swing
approval came back "order gate blocked:
dependency.watchdog.unhealthy,dependency.watchdog.stale — approve again", and
went back to pending. Approving again could never work, so the approvals
looped, and nothing told the operator that the health monitor was down.
"""
import datetime as datetime_module
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_orders import watchdog_notice  # noqa: E402
from swing_broker_harness import extract, source  # noqa: E402

FROZEN = datetime(2026, 10, 1, 5, 48, 52, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 2, 18, 39, 56, tzinfo=timezone.utc)
STALE = ("dependency.watchdog.unhealthy", "dependency.watchdog.stale")


# ---------------------------------------------------------------------------
# The advice
# ---------------------------------------------------------------------------

def test_a_silent_watchdog_is_called_down_and_asks_for_a_restart():
    advice = watchdog_notice.refusal_advice(STALE, FROZEN)
    assert "health monitor" in advice and "down" in advice
    assert "2026-10-01 05:48:52 UTC" in advice
    assert "restart" in advice
    assert "approve again" not in advice


def test_a_watchdog_that_never_reported_is_called_down():
    advice = watchdog_notice.refusal_advice(
        ("dependency.watchdog.unknown", "dependency.watchdog.stale"), None)
    assert "down" in advice and "never reported" in advice
    assert "restart" in advice and "approve again" not in advice


def test_a_watchdog_that_reports_unhealthy_is_not_called_down():
    advice = watchdog_notice.refusal_advice(
        ("dependency.watchdog.unhealthy",), LATER)
    assert "reports unhealthy" in advice and "down" not in advice
    assert "restart" in advice and "approve again" not in advice


def test_only_watchdog_codes_count():
    assert watchdog_notice.watchdog_codes(
        ("dependency.cash.stale",) + STALE) == STALE
    assert watchdog_notice.watchdog_codes(("dependency.cash.stale",)) == ()


# ---------------------------------------------------------------------------
# One alert per outage
# ---------------------------------------------------------------------------

def test_one_alert_per_stale_episode_not_per_refusal():
    latch = watchdog_notice.WatchdogOutageLatch()
    assert latch.should_alert("swing-paper", STALE, FROZEN, now=LATER) is True
    for minutes in range(1, 30):
        assert latch.should_alert("swing-paper", STALE, FROZEN,
                                  now=LATER + timedelta(minutes=minutes)) is False


def test_a_new_outage_alerts_again():
    latch = watchdog_notice.WatchdogOutageLatch()
    assert latch.should_alert("swing-paper", STALE, FROZEN, now=LATER)
    # The watchdog came back, reported, then went silent at a new time.
    second = LATER + timedelta(hours=2)
    assert latch.should_alert("swing-paper", STALE, second,
                              now=second + timedelta(minutes=10)) is True


def test_each_instance_has_its_own_episode():
    latch = watchdog_notice.WatchdogOutageLatch()
    assert latch.should_alert("swing-paper", STALE, FROZEN, now=LATER)
    assert latch.should_alert("alpaca-main", STALE, FROZEN, now=LATER)


def test_a_brief_gap_the_supervisor_heals_does_not_alert():
    latch = watchdog_notice.WatchdogOutageLatch()
    young = LATER - timedelta(seconds=90)
    assert latch.should_alert("swing-paper", STALE, young, now=LATER) is False
    # It does once the gap outlives the alert threshold.
    assert latch.should_alert("swing-paper", STALE, young,
                              now=LATER + timedelta(minutes=10)) is True


def test_a_monitor_that_is_up_and_reporting_unhealthy_does_not_alert_as_down():
    latch = watchdog_notice.WatchdogOutageLatch()
    assert latch.should_alert("swing-paper", ("dependency.watchdog.unhealthy",),
                              LATER - timedelta(seconds=20), now=LATER) is False


def test_a_refusal_without_watchdog_codes_never_alerts():
    latch = watchdog_notice.WatchdogOutageLatch()
    assert latch.should_alert("swing-paper", ("dependency.cash.stale",),
                              FROZEN, now=LATER) is False


def test_a_watchdog_that_never_reported_alerts_once():
    latch = watchdog_notice.WatchdogOutageLatch()
    codes = ("dependency.watchdog.unknown", "dependency.watchdog.stale")
    assert latch.should_alert("swing-paper", codes, None, now=LATER) is True
    assert latch.should_alert("swing-paper", codes, None,
                              now=LATER + timedelta(hours=1)) is False


# ---------------------------------------------------------------------------
# The alert and its notification type
# ---------------------------------------------------------------------------

def test_the_alert_goes_through_notify_as_watchdog_down(monkeypatch):
    import live_alerts
    sent = []
    monkeypatch.setattr(live_alerts, "notify", lambda **kw: sent.append(kw))
    live_alerts.alert_watchdog_down(instance_id="swing-paper", codes=STALE,
                                    last_report=FROZEN)
    (call,) = sent
    assert call["category"] == "watchdog_down"
    assert call["instance_id"] == "swing-paper"
    assert call["body"].startswith("WATCHDOG DOWN [swing-paper]")
    assert "2026-10-01 05:48:52 UTC" in call["body"]
    assert "restart" in call["body"].lower()
    assert call["discord_channel"] == "notifications"


def test_the_alert_never_raises(monkeypatch):
    import live_alerts

    def boom(**_kw):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(live_alerts, "notify", boom)
    live_alerts.alert_watchdog_down(instance_id="swing-paper", codes=STALE,
                                    last_report=None)


def test_watchdog_down_is_a_routable_push_on_type():
    from notification_types import (
        NOTIFICATION_TYPE_KEYS, _PUSH_ON_BY_DEFAULT, classify, type_for_key)
    assert "watchdog_down" in NOTIFICATION_TYPE_KEYS
    meta = type_for_key("watchdog_down")
    assert meta["group"] == "Risk & Halts"
    assert meta["channel"] == "notifications"
    assert "watchdog_down" in _PUSH_ON_BY_DEFAULT
    assert classify(content="WATCHDOG DOWN [alpaca-main] silent") == "watchdog_down"


# ---------------------------------------------------------------------------
# broker.py: the alert helper, and the tick path that calls it
# ---------------------------------------------------------------------------

def _helper(state=None):
    return extract(
        ("_alert_watchdog_gate_refusal",),
        assigns=("_WATCHDOG_OUTAGE_LATCH",),
        namespace={"datetime": datetime_module,
                   "_live_order_dependency_lock": threading.RLock(),
                   "_live_order_dependency_state": dict(state or {})})


def test_the_broker_helper_alerts_once_per_outage(monkeypatch):
    import live_alerts
    sent = []
    monkeypatch.setattr(live_alerts, "alert_watchdog_down",
                        lambda **kw: sent.append(kw))
    ns = _helper()
    helper = ns["_alert_watchdog_gate_refusal"]
    logs = []
    say = lambda message, color="white": logs.append((color, message))  # noqa: E731
    assert helper("swing-paper", list(STALE), FROZEN, log=say, now=LATER) is True
    assert helper("swing-paper", list(STALE), FROZEN, log=say,
                  now=LATER + timedelta(minutes=20)) is False
    assert sent == [{"instance_id": "swing-paper", "codes": STALE,
                     "last_report": FROZEN}]
    assert any(color == "red" and "health monitor" in message
               for color, message in logs)


def test_the_broker_helper_reads_the_loops_watchdog_stamp_on_the_tick_path(
        monkeypatch):
    import live_alerts
    sent = []
    monkeypatch.setattr(live_alerts, "alert_watchdog_down",
                        lambda **kw: sent.append(kw))
    helper = _helper({"watchdog_at": FROZEN})["_alert_watchdog_gate_refusal"]
    assert helper("alpaca-main", STALE, from_loop_state=True, now=LATER) is True
    assert sent[0]["last_report"] == FROZEN


def test_the_broker_helper_ignores_other_refusals_and_never_raises(monkeypatch):
    import live_alerts

    def boom(**_kw):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(live_alerts, "alert_watchdog_down", boom)
    helper = _helper()["_alert_watchdog_gate_refusal"]
    assert helper("swing-paper", ("cash.insufficient",), FROZEN, now=LATER) is False
    assert helper("swing-paper", STALE, FROZEN, now=LATER) is False


def test_the_ticks_equity_gate_refusal_calls_the_helper():
    """The tick's equity submit block is inline module-level code. The call
    sits after the blocked/uncertain chain, inside the equity branch, behind
    its own `not allowed` check."""
    text = source()
    at = text.index('f"ORDER GATE BLOCKED "')
    end = text.index("# Non-equity compatibility paths are", at)
    block = text[at:end]
    call = block.index("_alert_watchdog_gate_refusal(")
    assert block.rindex("if not _submission.decision.allowed:", 0, call) > \
        block.index('outcome="uncertain"')
    assert "from_loop_state=True" in block[call:]
