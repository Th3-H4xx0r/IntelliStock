"""`fill_at_next_open` orders fill at the next session's OPEN (spec 6.2)."""
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
#: Decision at 05:00 PT Monday (naive UTC, as the broker clock carries it).
DECISION = datetime(2026, 3, 2, 13, 0)
MON_OPEN = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
MON_CLOSE = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)
TUE_OPEN = MON_OPEN + timedelta(days=1)
TUE_CLOSE = MON_CLOSE + timedelta(days=1)


def _order(**overrides):
    values = dict(order_id="o1", symbol="AAPL", side="buy", quantity=10.0,
                  decision_at=DECISION, execute_not_before=DECISION,
                  source="main_signal", fill_at_next_open=True)
    values.update(overrides)
    return SimulationOrder(**values)


def _bar(opened=MON_OPEN, known=MON_CLOSE, o=100.0, h=104.0, l=97.0, c=102.0,
         symbol="AAPL"):
    return SimulationBarEvent(symbol=symbol, open=o, high=h, low=l, close=c,
                              bar_ts=opened, available_at=known)


def test_an_ordinary_quote_never_fills_a_next_open_order():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    quote = SimulationQuote.from_mid(symbol="AAPL", timestamp=MON_CLOSE,
                                     mid=102.0, spread_bps=20.0)
    assert sim.on_quote(quote) == ()
    assert sim.pending_order_count == 1


def test_a_buy_fills_at_the_open_with_the_market_cost_model():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    [fill] = sim.on_bar(_bar(o=100.0))
    touch = 100.0 * (1 + 20.0 / 20_000)
    assert fill.price == pytest.approx(touch * (1 + 10.0 / 10_000))
    assert fill.spread_cost == pytest.approx((touch - 100.0) * 10)
    assert fill.fees == pytest.approx(10 * fill.price * 5.0 / 10_000)
    # Stamped at the bar's open -- not its close, not the tick's clock.
    assert fill.quote_timestamp == MON_OPEN
    assert fill.executed_at == MON_OPEN
    assert fill.source == "main_signal"
    assert sim.pending_order_count == 0


def test_a_sell_fills_at_the_open_below_it():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(side="sell", quantity=4.0))
    [fill] = sim.on_bar(_bar(o=100.0))
    touch = 100.0 * (1 - 20.0 / 20_000)
    assert fill.price == pytest.approx(touch * (1 - 10.0 / 10_000))


def test_a_bar_that_opened_before_the_decision_is_not_the_next_open():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(decision_at=datetime(2026, 3, 2, 15, 0),
                      execute_not_before=datetime(2026, 3, 2, 15, 0)))
    assert sim.on_bar(_bar()) == []            # Monday opened at 14:30
    assert sim.pending_order_count == 1
    [fill] = sim.on_bar(_bar(opened=TUE_OPEN, known=TUE_CLOSE, o=103.0))
    assert fill.quote_timestamp == TUE_OPEN


def test_one_shot_an_unfunded_order_is_dropped_and_counted_not_left_pending():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    assert sim.on_bar(_bar(), cash_budget=lambda: 0.0) == []
    assert sim.pending_order_count == 0
    summary = sim.execution_summary()
    assert summary["unfilled_order_count"] == 0
    assert summary["next_open_order_count"] == 1
    assert summary["next_open_expired_order_count"] == 1
    # It never fills at a later, different open.
    assert sim.on_bar(_bar(opened=TUE_OPEN, known=TUE_CLOSE)) == []


def test_a_cash_clamp_floors_a_whole_share_order_and_the_rest_expires():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(whole_shares=True))
    [fill] = sim.on_bar(_bar(o=100.0), cash_budget=lambda: 750.0)
    assert fill.incremental_quantity == 7.0          # 7.48 affordable -> 7
    assert fill.is_final is False
    assert sim.pending_order_count == 0
    assert sim.execution_summary()["next_open_expired_order_count"] == 1


def test_a_fractional_order_keeps_its_fractional_clamp_as_before():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    [fill] = sim.on_bar(_bar(o=100.0), cash_budget=lambda: 750.0)
    assert 7.4 < fill.incremental_quantity < 7.5


def test_a_processed_bar_is_skipped_when_sent_again():
    """Pins the per-symbol cursor. The broker never dates an order before a
    bar it has already processed; the second order here does so only to
    prove a resent bar is ignored."""
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    assert len(sim.on_bar(_bar())) == 1
    sim.submit(_order(order_id="o2", decision_at=DECISION - timedelta(days=3),
                      execute_not_before=DECISION - timedelta(days=3)))
    assert sim.on_bar(_bar()) == []
    assert sim.pending_order_count == 1


def test_requirements_name_the_decision_then_the_last_bar_seen():
    sim = NextEventExecutionSimulator(COSTS)
    assert sim.bar_event_requirements() == {}
    sim.submit(_order())
    assert sim.has_next_open_orders is True
    assert sim.bar_event_requirements() == {
        "AAPL": DECISION.replace(tzinfo=timezone.utc)}
    sim.on_bar(_bar())
    assert sim.has_next_open_orders is False
    sim.submit(_order(order_id="o2", decision_at=DECISION - timedelta(days=3),
                      execute_not_before=DECISION - timedelta(days=3)))
    assert sim.bar_event_requirements() == {"AAPL": MON_OPEN}


def test_a_run_without_next_open_orders_has_no_new_summary_keys():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(fill_at_next_open=False,
                      execute_not_before=DECISION + timedelta(days=1)))
    summary = sim.execution_summary()
    assert "next_open_order_count" not in summary
    assert "next_open_expired_order_count" not in summary
    assert "bracket_order_count" not in summary
