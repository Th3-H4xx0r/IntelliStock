"""Bracket legs trigger off each bar's high and low (spec 6.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationBarEvent,
    SimulationOrder,
    SimulationQuote,
)

COSTS = ExecutionCostModel(version="test-v1", spread_bps=20.0,
                           slippage_bps=10.0, fee_bps=5.0,
                           latency=timedelta(0))
DECISION = datetime(2026, 3, 2, 13, 0)
DAY = timedelta(days=1)
OPEN0 = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
CLOSE0 = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)
STOP, TARGET = 94.0, 109.0


def _bar(day=0, o=100.0, h=104.0, l=97.0, c=102.0):
    return SimulationBarEvent(symbol="AAPL", open=o, high=h, low=l, close=c,
                              bar_ts=OPEN0 + day * DAY,
                              available_at=CLOSE0 + day * DAY)


def _parent(**overrides):
    values = dict(order_id="p1", symbol="AAPL", side="buy", quantity=10.0,
                  decision_at=DECISION, execute_not_before=DECISION,
                  source="main_signal", fill_at_next_open=True,
                  whole_shares=True,
                  bracket={"take_profit_price": TARGET,
                           "stop_loss_price": STOP})
    values.update(overrides)
    return SimulationOrder(**values)


def _armed():
    """A 10-share parent filled at Monday's open, legs armed, Monday quiet."""
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent())
    [buy] = sim.on_bar(_bar(0))
    assert buy.side == "buy"
    return sim


def test_a_filled_parent_arms_legs_that_are_not_pending_orders():
    sim = _armed()
    [leg] = sim.bracket_legs
    assert leg == {"parent_id": "p1", "symbol": "AAPL", "qty": 10.0,
                   "stop_loss_price": STOP, "take_profit_price": TARGET,
                   "armed_from_bar_ts": OPEN0}
    assert sim.has_bracket_legs is True
    assert sim.pending_orders == ()
    assert sim.pending_symbols == ()
    assert sim.execution_summary()["unfilled_order_count"] == 0


def _stop_price(trigger):
    touch = trigger * (1 - 20.0 / 20_000)
    return touch, touch * (1 - 10.0 / 10_000)


@pytest.mark.parametrize("rule, bar, source, reason, price, stamped", [
    ("1 gap through the stop", dict(o=93.0, h=95.0, l=90.0, c=91.0),
     "bracket_sl_gap:p1", "stop_loss", _stop_price(93.0)[1], "open"),
    ("2 gap through the target", dict(o=111.0, h=113.0, l=108.0, c=112.0),
     "bracket_tp_gap:p1", "take_profit", 111.0, "open"),
    ("3 both touched: the stop wins", dict(o=100.0, h=110.0, l=93.0, c=100.0),
     "bracket_sl:p1", "stop_loss", _stop_price(STOP)[1], "close"),
    ("4 low reaches the stop", dict(o=100.0, h=104.0, l=94.0, c=95.0),
     "bracket_sl:p1", "stop_loss", _stop_price(STOP)[1], "close"),
    ("5 high reaches the target", dict(o=100.0, h=109.0, l=99.0, c=108.0),
     "bracket_tp:p1", "take_profit", TARGET, "close"),
])
def test_each_trigger_rule(rule, bar, source, reason, price, stamped):
    sim = _armed()
    [fill] = sim.on_bar(_bar(1, **bar))
    assert fill.side == "sell"
    assert fill.source == source
    assert fill.exit_reason == reason
    assert fill.order_id == f"p1:{'sl' if reason == 'stop_loss' else 'tp'}"
    assert fill.incremental_quantity == fill.cumulative_quantity == 10.0
    assert fill.price == pytest.approx(price)
    assert fill.fees == pytest.approx(10.0 * price * 5.0 / 10_000)
    when = (OPEN0 if stamped == "open" else CLOSE0) + DAY
    assert fill.quote_timestamp == fill.executed_at == when
    assert sim.bracket_legs == ()


def test_a_split_shaped_gap_is_traded_as_a_gap():
    """Bars are fetched adjustment=split, so a halving in the cache is a real
    move or a stale cache to purge -- the reading that keeps the emulator's
    split reconcile off. No special case: the stop fills at the open."""
    sim = _armed()
    [stop] = sim.on_bar(_bar(1, o=50.5, h=51.0, l=50.0, c=50.7))
    assert stop.source == "bracket_sl_gap:p1"
    assert stop.price == pytest.approx(_stop_price(50.5)[1])


def test_a_quiet_bar_triggers_nothing():
    sim = _armed()
    assert sim.on_bar(_bar(1, o=100.0, h=108.99, l=94.01, c=101.0)) == []
    assert len(sim.bracket_legs) == 1


def test_the_stop_pays_spread_and_slippage_the_target_pays_neither():
    sim = _armed()
    [stop] = sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0))
    touch, price = _stop_price(STOP)
    assert stop.spread_cost == pytest.approx((STOP - touch) * 10)
    assert stop.slippage_cost == pytest.approx((touch - price) * 10)
    sim = _armed()
    [target] = sim.on_bar(_bar(1, o=100.0, h=120.0, l=99.0, c=118.0))
    assert target.spread_cost == 0.0
    assert target.slippage_cost == 0.0
    assert target.fees > 0.0


def test_the_parents_own_fill_bar_is_checked():
    """Filled at the open, so the whole bar's range happened after the fill."""
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent())
    buy, stop = sim.on_bar(_bar(0, o=100.0, h=101.0, l=93.0, c=94.5))
    assert (buy.side, stop.source) == ("buy", "bracket_sl:p1")


def test_an_open_below_the_stop_buys_and_stops_out_at_the_same_open():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent())
    buy, stop = sim.on_bar(_bar(0, o=92.0, h=95.0, l=91.0, c=93.0))
    assert buy.quote_timestamp == stop.quote_timestamp == OPEN0
    assert stop.source == "bracket_sl_gap:p1"
    assert stop.price < buy.price


def test_a_parent_filled_at_a_close_is_checked_from_the_next_bar():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent(fill_at_next_open=False,
                       execute_not_before=DECISION + DAY))
    quote = SimulationQuote.from_mid(symbol="AAPL", timestamp=CLOSE0 + DAY,
                                     mid=100.0, spread_bps=20.0)
    [buy] = sim.on_quote(quote)
    assert sim.bracket_legs[0]["armed_from_bar_ts"] == CLOSE0 + DAY
    # Tuesday's range happened BEFORE the fill at Tuesday's close.
    assert sim.on_bar(_bar(1, o=100.0, h=101.0, l=80.0, c=100.0)) == []
    [stop] = sim.on_bar(_bar(2, o=99.0, h=100.0, l=93.0, c=95.0))
    assert stop.source == "bracket_sl:p1"


def test_one_cancels_other_and_the_pending_strategy_sell_is_cancelled():
    sim = _armed()
    sim.submit(SimulationOrder(
        order_id="s1", symbol="AAPL", side="sell", quantity=10.0,
        decision_at=CLOSE0 + DAY, execute_not_before=CLOSE0 + 2 * DAY,
        source="main_signal"))
    [stop] = sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0))
    assert stop.exit_reason == "stop_loss"
    assert sim.pending_orders == ()
    # The sibling target is gone: a later bar through it fills nothing.
    assert sim.on_bar(_bar(2, o=100.0, h=130.0, l=99.0, c=120.0)) == []
    summary = sim.execution_summary()
    assert summary["cancelled_order_count"] == 1
    assert summary["unfilled_order_count"] == 0
    assert summary["bracket_exit_counts"] == {
        "stop_loss": 1, "stop_loss_gap": 0,
        "take_profit": 0, "take_profit_gap": 0}
    assert summary["bracket_open_leg_count"] == 0
    assert summary["bracket_order_count"] == 1


def test_a_strategy_sell_that_fills_shrinks_then_deletes_the_legs():
    sim = _armed()
    steps = (("s1", 4.0, 6.0), ("s2", 6.0, None))
    for minute, (order_id, qty, legs_left) in enumerate(steps, start=1):
        sim.submit(SimulationOrder(
            order_id=order_id, symbol="AAPL", side="sell", quantity=qty,
            decision_at=CLOSE0, execute_not_before=CLOSE0,
            source="main_signal"))
        quote = SimulationQuote.from_mid(
            symbol="AAPL", timestamp=CLOSE0 + timedelta(minutes=minute),
            mid=100.0, spread_bps=20.0)
        [sell] = sim.on_quote(quote)
        assert sell.incremental_quantity == qty
        legs = sim.bracket_legs
        assert (legs[0]["qty"] if legs else None) == legs_left


def test_a_leg_sells_no_more_than_is_held():
    sim = _armed()
    [stop] = sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0),
                        position_of=lambda symbol: 3.0)
    assert stop.incremental_quantity == 3.0


def test_a_leg_with_nothing_left_to_protect_is_dropped_without_a_fill():
    sim = _armed()
    assert sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0),
                      position_of=lambda symbol: 0.0) == []
    assert sim.bracket_legs == ()


def test_legs_are_requirements_until_they_close():
    sim = _armed()
    assert sim.bar_event_requirements() == {"AAPL": OPEN0}
    sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0))
    assert sim.bar_event_requirements() == {}
