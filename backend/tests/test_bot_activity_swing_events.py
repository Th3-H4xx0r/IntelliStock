"""Bot activity includes the trades the swing/wheel approval lane sent.

The lane never writes BotTradeDecisions, so a wheel put the bot sold showed
"No bot trades logged yet" on its stock screen (operator report 2026-10-02).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import interactive_utils  # noqa: E402
from swing_trader import signals_store  # noqa: E402

IID = "swing-paper"
CONTRACT = "QCOM261009P00177500"


@pytest.fixture
def stores(store, monkeypatch):
    monkeypatch.setattr(interactive_utils, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    monkeypatch.setattr(interactive_utils, "ensure_bot_trade_decisions_table", lambda conn: None)
    return store


def wheel_signal(status="submitted", *, with_order=True, symbol="QCOM"):
    doc = signals_store.new_signal(
        instance_id=IID, lane="wheel", symbol=symbol, session="2026-09-28", score=70,
        recommendation="REVIEW", reasoning="Sell the 177.5 put for premium.", key_risks=[],
        size_adjustment=None, proposal={"qty": 1, "expiry": "2026-10-09", "strike": 184.15},
        status=status, created_at="2026-09-28T14:45:33+00:00")
    doc["claimed_at"] = "2026-10-02T19:24:51+00:00"
    if with_order:
        doc["submitted_order"] = {
            "kind": "option", "contract": CONTRACT, "underlying": symbol, "qty": 1,
            "limit_price": 1.23, "option_type": "put", "position_intent": "sell_to_open",
            "strike": 177.5, "expiry": "2026-10-09",
        }
        doc["outcome"] = {"contracts": 1, "premium_received": 1.31}
    signals_store.insert_signal(doc)
    return doc


def test_a_sent_wheel_put_is_an_event_on_its_contract(stores):
    wheel_signal()
    out = interactive_utils.action_list_bot_trade_decisions(
        None, "brk-1", symbol=CONTRACT, instance_id=IID)
    assert out["total"] == 1
    event = out["events"][0]
    assert event["symbol"] == CONTRACT
    assert event["side"] == "sell"
    assert event["price"] == 1.31
    assert event["strategy"] == "Wheel · sell to open put"
    assert event["reason"] == "Sell the 177.5 put for premium."
    assert event["score"] == 70
    assert event["ts"] == "2026-10-02T19:24:51+00:00"


def test_the_underlying_screen_shows_it_too(stores):
    wheel_signal()
    out = interactive_utils.action_list_bot_trade_decisions(
        None, "brk-1", symbol="qcom", instance_id=IID)
    assert [e["symbol"] for e in out["events"]] == [CONTRACT]


def test_signals_that_never_sent_an_order_are_not_trades(stores):
    wheel_signal(status="rejected", with_order=False)
    wheel_signal(status="pending", with_order=False)
    out = interactive_utils.action_list_bot_trade_decisions(
        None, "brk-1", symbol=CONTRACT, instance_id=IID)
    assert out["events"] == []


def test_no_instance_means_decisions_only(stores):
    wheel_signal()
    out = interactive_utils.action_list_bot_trade_decisions(None, "brk-1", symbol=CONTRACT)
    assert out["events"] == []


def test_merged_newest_first_with_the_decision_log(stores):
    wheel_signal()
    stores.insert(interactive_utils.BOT_TRADE_DECISIONS_TABLE, [{
        "id": "d1", "brokerage_id": "brk-1", "symbol": "QCOM", "side": "buy",
        "ts": "2026-10-03T14:00:00+00:00", "reason": "core buy",
    }])
    out = interactive_utils.action_list_bot_trade_decisions(
        None, "brk-1", symbol="QCOM", instance_id=IID)
    assert [e["side"] for e in out["events"]] == ["buy", "sell"]
    assert out["total"] == 2
