"""PortfolioEmulator plumbing for next-open fills and bracket legs (spec 6.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
)

COSTS = ExecutionCostModel(version="test-v1", spread_bps=20.0,
                           slippage_bps=10.0, fee_bps=5.0,
                           latency=timedelta(0))
DAY = timedelta(days=1)
DECISION = datetime(2026, 3, 2, 13, 0)          # Monday 05:00 PT, naive UTC
OPEN0 = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
CLOSE0 = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)
BRACKET = {"take_profit_price": 109.0, "stop_loss_price": 94.0}


def _emulator(cash=10_000.0):
    sim = NextEventExecutionSimulator(COSTS)
    return PortfolioEmulator(cash, execution_simulator=sim,
                             execution_delay=DAY), sim


def _bar(day=0, o=100.0, h=104.0, l=97.0, c=102.0):
    return {"t": f"2026-03-0{2 + day}T05:00:00Z", "o": o, "h": h, "l": l,
            "c": c, "bar_ts": OPEN0 + day * DAY,
            "available_at": CLOSE0 + day * DAY}


def _clock(day):
    """The tick that first sees bar `day`: 05:00 PT the next day."""
    return DECISION + (day + 1) * DAY


def _swing_buy(emu, symbol="AAPL", cash=1250.0, price=100.0, when=DECISION):
    return emu.execute_signal(
        symbol, 1, price, timestamp=when, cash_per_trade=cash,
        order_source="main_signal", bracket=dict(BRACKET), whole_shares=True,
        fill_at_next_open=True)


def test_no_simulator_means_no_bar_work():
    emu = PortfolioEmulator(1_000.0)
    assert emu.has_bracket_legs() is False
    assert emu.has_next_open_orders() is False
    assert emu.bar_event_requirements() == {}
    assert emu.process_bar_events({"AAPL": [_bar()]}, _clock(0)) == []


def test_a_whole_share_next_open_order_is_a_quantity_order_with_no_delay():
    emu, sim = _emulator()
    assert _swing_buy(emu)
    [order] = sim.pending_orders
    assert order.quantity == 12.0            # 1250 / all-in 100.25 = 12.47
    assert order.whole_shares is True
    assert order.notional_limit is None
    assert order.execute_not_before == order.decision_at == DECISION
    assert order.bracket == BRACKET
    assert emu.has_next_open_orders() is True


def test_a_whole_share_buy_too_small_for_one_share_is_not_submitted():
    emu, sim = _emulator()
    assert _swing_buy(emu, cash=90.0) is False
    assert sim.pending_orders == ()


def test_a_next_open_order_never_rests_passively():
    emu, sim = _emulator()
    PortfolioEmulator.set_passive_execution(True, 8)
    try:
        assert _swing_buy(emu)
        emu.execute_signal("MSFT", 1, 50.0, timestamp=DECISION,
                           cash_per_trade=500.0, order_source="main_signal")
    finally:
        PortfolioEmulator._PASSIVE_OVERRIDE = None
    next_open, ordinary = sim.pending_orders
    assert next_open.limit_price is None
    assert ordinary.limit_price == 50.0


def test_entry_fill_then_stop_books_trades_with_source_and_exit_reason():
    emu, sim = _emulator()
    _swing_buy(emu)
    fills = emu.process_bar_events({"AAPL": [_bar(0)]}, _clock(0))
    assert [f.side for f in fills] == ["buy"]
    assert emu.get_positions() == {"AAPL": 12.0}
    assert emu.has_bracket_legs() is True
    assert emu.pending_execution_symbols() == ()
    assert emu._execution_cash_reservations == {}

    fills = emu.process_bar_events(
        {"AAPL": [_bar(1, o=100.0, h=101.0, l=90.0, c=91.0)]}, _clock(1))
    assert [f.source for f in fills] == [f"bracket_sl:{sim.fills[0].order_id}"]
    assert emu.get_positions() == {}
    buy_row, stop_row = emu.get_trade_history()
    assert "exit_reason" not in buy_row
    assert stop_row["exit_reason"] == "stop_loss"
    assert stop_row["source"].startswith("bracket_sl:")
    assert stop_row["timestamp"] == CLOSE0 + DAY
    summary = emu.get_execution_summary()
    assert summary["unfilled_order_count"] == 0
    assert summary["bracket_exit_counts"]["stop_loss"] == 1


def test_a_stop_cancels_the_pending_strategy_sell_and_its_reservation():
    emu, sim = _emulator()
    _swing_buy(emu)
    emu.process_bar_events({"AAPL": [_bar(0)]}, _clock(0))
    # An ordinary (close-filled) strategy sell, waiting for Wednesday's close.
    assert emu.execute_signal("AAPL", -1, 102.0, timestamp=_clock(0),
                              sell_fraction=1.0, order_source="main_signal")
    assert emu.pending_execution_symbols() == ("AAPL",)
    emu.process_bar_events(
        {"AAPL": [_bar(1, o=100.0, h=101.0, l=90.0, c=91.0)]}, _clock(1))
    assert emu.pending_execution_symbols() == ()
    assert emu._execution_position_reservations == {}
    assert emu.get_execution_summary()["cancelled_order_count"] == 1


def test_at_one_open_the_exit_funds_the_entry():
    """MSFT's RSI exit and AAPL's entry both fill at Tuesday's open. Sorted
    by name, AAPL would go first and find no cash. (The entry is SIZED against
    the pending sale only with backtest_credit_pending_sell_proceeds on.)"""
    emu, sim = _emulator(cash=1_300.0)
    emu.credit_pending_sell_proceeds = True
    emu.execute_signal("MSFT", 1, 100.0, timestamp=DECISION,
                       cash_per_trade=1_250.0, order_source="main_signal",
                       whole_shares=True, fill_at_next_open=True)
    emu.process_bar_events({"MSFT": [_bar(0)]}, _clock(0))
    assert emu.get_positions() == {"MSFT": 12.0}
    emu.execute_signal("MSFT", -1, 102.0, timestamp=_clock(0),
                       sell_fraction=1.0, order_source="main_signal",
                       fill_at_next_open=True)
    _swing_buy(emu, cash=1_100.0, price=102.0, when=_clock(0))
    fills = emu.process_bar_events(
        {"AAPL": [_bar(1, o=101.0)], "MSFT": [_bar(1, o=103.0)]}, _clock(1))
    assert [(f.symbol, f.side) for f in fills] == [
        ("MSFT", "sell"), ("AAPL", "buy")]
    assert emu.get_positions()["AAPL"] == 10.0


def test_an_rsi_exit_at_the_open_beats_a_stop_later_in_the_same_bar():
    """Live, the engine cancels the legs before the exit's market order
    reaches the open auction; the backtest must agree."""
    emu, _sim = _emulator()
    _swing_buy(emu)
    emu.process_bar_events({"AAPL": [_bar(0)]}, _clock(0))
    emu.execute_signal("AAPL", -1, 102.0, timestamp=_clock(0),
                       sell_fraction=1.0, order_source="main_signal",
                       fill_at_next_open=True)
    [sell] = emu.process_bar_events(
        {"AAPL": [_bar(1, o=100.0, h=101.0, l=90.0, c=92.0)]}, _clock(1))
    assert (sell.source, sell.quote_timestamp) == ("main_signal", OPEN0 + DAY)
    summary = emu.get_execution_summary()
    assert summary["bracket_exit_counts"] == {
        "stop_loss": 0, "stop_loss_gap": 0,
        "take_profit": 0, "take_profit_gap": 0}
    assert summary["bracket_open_leg_count"] == 0
    assert emu.get_positions() == {}


def test_an_entry_the_cash_cannot_fully_pay_for_protects_what_it_bought():
    """Sized at 9 shares on a $100 close; the open gaps up 15%, so the cash
    buys 8. The legs cover 8, the ninth share is dropped and counted, and the
    stop sells exactly 8."""
    emu, sim = _emulator(cash=1_000.0)
    emu.execute_signal("AAPL", 1, 100.0, timestamp=DECISION,
                       cash_per_trade=1_000.0, order_source="main_signal",
                       whole_shares=True, fill_at_next_open=True,
                       bracket={"take_profit_price": 125.0,
                                "stop_loss_price": 108.0})
    [buy] = emu.process_bar_events(
        {"AAPL": [_bar(0, o=115.0, h=116.0, l=114.0, c=115.0)]}, _clock(0))
    assert (buy.incremental_quantity, buy.order_quantity) == (8.0, 9.0)
    assert [leg["qty"] for leg in sim.bracket_legs] == [8.0]
    [stop] = emu.process_bar_events(
        {"AAPL": [_bar(1, o=115.0, h=115.0, l=90.0, c=91.0)]}, _clock(1))
    assert stop.incremental_quantity == 8.0
    summary = emu.get_execution_summary()
    assert summary["next_open_expired_order_count"] == 1
    assert summary["unfilled_order_count"] == 0
    assert emu.get_positions() == {}


def test_a_bar_from_the_future_is_a_look_ahead_bug():
    emu, _sim = _emulator()
    _swing_buy(emu)
    with pytest.raises(ValueError, match="not available"):
        emu.process_bar_events({"AAPL": [_bar(0)]}, CLOSE0 - timedelta(hours=1))


def test_requirements_pass_through():
    emu, _sim = _emulator()
    _swing_buy(emu)
    assert emu.bar_event_requirements() == {
        "AAPL": DECISION.replace(tzinfo=timezone.utc)}
