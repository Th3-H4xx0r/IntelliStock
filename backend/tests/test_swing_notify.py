"""The eight swing/wheel notification types and their sender."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from notification_types import (  # noqa: E402
    NOTIFICATION_TYPE_KEYS, _PUSH_ON_BY_DEFAULT, classify, default_routing,
    groups_in_order, type_for_key,
)
from swing_trader import notify  # noqa: E402

SWING_KEYS = ("swing_entry", "swing_pending_review", "swing_exit",
              "swing_run_summary", "wheel_put_placed", "wheel_pending_review",
              "wheel_position_alert", "wheel_assignment")
PUSH_ON = {"swing_entry", "swing_pending_review", "swing_exit",
           "wheel_put_placed", "wheel_pending_review", "wheel_position_alert",
           "wheel_assignment"}   # fix wave item 4: ST pushed assignments


def test_the_eight_types_exist_in_their_own_group():
    for key in SWING_KEYS:
        assert key in NOTIFICATION_TYPE_KEYS
        assert type_for_key(key)["group"] == "Swing & Wheel"
    assert "Swing & Wheel" in groups_in_order()


def test_reviews_entries_exits_and_alerts_push_by_default():
    routing = default_routing()
    for key in SWING_KEYS:
        assert routing[key]["discord"] is True
        assert routing[key]["push"] is (key in PUSH_ON)
    assert PUSH_ON <= _PUSH_ON_BY_DEFAULT


def test_each_prefix_classifies_to_its_own_key():
    for key in SWING_KEYS:
        text = f"{notify.PREFIXES[key]} [swing-paper] something"
        assert classify(content=text) == key


def test_send_routes_by_category_with_the_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    notify.send("swing_pending_review", "swing-paper", "⚠️ REVIEW: AAPL (score 62/100)",
                "AAPL @ $190.00\nScore: 62/100 — needs your approval", priority=1)
    assert len(sent) == 1
    msg = sent[0]
    assert msg["category"] == "swing_pending_review"
    assert msg["instance_id"] == "swing-paper"
    assert msg["discord_channel"] == "trades"
    assert msg["body"].startswith("SWING REVIEW [swing-paper] ⚠️ REVIEW: AAPL")
    assert msg["push_title"] == "⚠️ REVIEW: AAPL (score 62/100)"


def test_priority_two_is_marked_urgent(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    notify.send("wheel_position_alert", "swing-paper", "🚨 Auto-Closed: APH", "x", priority=2)
    assert "(URGENT)" in sent[0]["body"] and "(URGENT)" in sent[0]["push_title"]


def test_send_never_raises(monkeypatch):
    def boom(**kw):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(notify, "_sink", boom)
    notify.send("swing_entry", "swing-paper", "BUY AAPL", "x")


def test_wheel_assignment_helper(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    notify.notify_wheel_assignment("swing-paper", symbol="APH", qty=100, price=130.0,
                                   date="2026-10-02")
    assert sent[0]["category"] == "wheel_assignment"
    assert "APH: assigned 100 shares @ $130.00 on 2026-10-02" in sent[0]["body"]


# -- the ninth type (plan C final review): an approved order the broker refused --

def test_swing_approval_failed_pushes_and_classifies():
    assert type_for_key("swing_approval_failed")["group"] == "Swing & Wheel"
    assert default_routing()["swing_approval_failed"] == {"discord": True, "push": True}
    assert classify(content=f"{notify.PREFIXES['swing_approval_failed']} [swing-paper] x") \
        == "swing_approval_failed"


def test_swing_approval_failed_helper(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    notify.notify_swing_approval_failed("swing-paper", symbol="AAPL", lane="swing",
                                        reason="insufficient buying power")
    msg = sent[0]
    assert msg["category"] == "swing_approval_failed"
    assert msg["discord_channel"] == "trades"
    assert msg["body"].startswith("SWING APPROVAL FAILED [swing-paper] ")
    assert "AAPL" in msg["push_title"]
    assert "insufficient buying power" in msg["body"]
    assert "swing" in msg["body"]


def test_the_helpers_never_raise(monkeypatch):
    def boom(**kw):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(notify, "_sink", boom)
    notify.notify_swing_approval_failed("swing-paper", symbol="AAPL", lane="wheel",
                                        reason="x")
    notify.notify_wheel_assignment("swing-paper", symbol="APH", qty="not-a-number")
