"""Swing-port backtest scenarios on real NYSE sessions (spec 6.2).

Each test drives the emulator tick by tick in the broker's own order -- the
pending-fill block on the latest close, then the bar hook, then the
"strategy" (scripted here) -- over daily bars labelled the way Alpaca labels
them. Two inputs the spec is silent on: a halt, and several bars arriving
in one tick.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import backtest_bar_events as bbe  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationPriceEvent,
)
from swing_port_calendar_fixtures import (  # noqa: E402
    AVAILABLE,
    OPENS,
    daily_bar as _bar,
)

UTC = timezone.utc
COSTS = ExecutionCostModel(version="test-v1", spread_bps=20.0,
                           slippage_bps=10.0, fee_bps=5.0,
                           latency=timedelta(0))
BRACKET = {"take_profit_price": 109.0, "stop_loss_price": 94.0}


def _at(date):
    """05:00 PT that day on the broker's naive-UTC clock (PST: 13:00 UTC)."""
    return datetime.fromisoformat(f"{date}T13:00:00")


def _emulator():
    bbe.reset_label_cache()
    return PortfolioEmulator(
        10_000.0, execution_simulator=NextEventExecutionSimulator(COSTS),
        execution_delay=timedelta(days=1))


def _tick(emu, data, now):
    """The broker's pre-strategy order: pending closes, then the bar hook."""
    clock = now.replace(tzinfo=UTC)
    events = {}
    for symbol in emu.pending_execution_symbols():
        visible = [b for b in data.get(symbol, ()) if AVAILABLE(b) <= clock]
        if visible:
            events[symbol] = SimulationPriceEvent(
                symbol=symbol, price=visible[-1]["c"],
                available_at=AVAILABLE(visible[-1]),
                bar_timestamp=datetime.fromisoformat(
                    visible[-1]["t"].replace("Z", "+00:00")))
    fills = list(emu.process_price_events(events))
    if emu.has_bracket_legs() or emu.has_next_open_orders():
        bars = bbe.collect_bar_events(
            data, emu.bar_event_requirements(), clock,
            bar_time_to_datetime=bar_time_to_datetime,
            bar_available_at=AVAILABLE, bar_open_at=OPENS)
        if bars:
            fills.extend(emu.process_bar_events(bars, clock))
    return fills


def _enter(emu, now, symbol="AAPL", cash=1_250.0):
    return emu.execute_signal(
        symbol, 1, 100.0, timestamp=now, cash_per_trade=cash,
        order_source="main_signal", bracket=dict(BRACKET), whole_shares=True,
        fill_at_next_open=True)


def test_a_halt_leaves_the_legs_armed_and_the_reopen_gap_is_honoured():
    data = {"AAPL": [_bar("2026-03-02", 100, 102, 99, 101),
                     # Tue-Thu: halted, no bars. Friday reopens far lower.
                     _bar("2026-03-06", 80, 82, 78, 81)]}
    emu = _emulator()
    _enter(emu, _at("2026-03-02"))
    _tick(emu, data, _at("2026-03-03"))
    for day in ("2026-03-04", "2026-03-05", "2026-03-06"):
        assert _tick(emu, data, _at(day)) == []
        assert emu.has_bracket_legs() is True
    [stop] = _tick(emu, data, _at("2026-03-09"))
    assert stop.source.startswith("bracket_sl_gap:")
    assert stop.price < 80.0                      # the open, less the spread
    assert stop.quote_timestamp == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)


def test_bars_that_arrive_together_are_replayed_in_order():
    """Decided Friday; the Tuesday tick is skipped (Monday is Presidents'
    Day). Wednesday's tick sees Friday AND Tuesday: the entry fills at
    FRIDAY's open and Tuesday's low stops it out."""
    data = {"AAPL": [_bar("2026-02-12", 100, 101, 99, 100),
                     _bar("2026-02-13", 101, 103, 100, 102),
                     _bar("2026-02-17", 99, 100, 93, 95)]}
    emu = _emulator()
    _enter(emu, _at("2026-02-13"))
    buy, stop = _tick(emu, data, _at("2026-02-18"))
    assert buy.quote_timestamp == datetime(2026, 2, 13, 14, 30, tzinfo=UTC)
    assert stop.source.startswith("bracket_sl:")
    assert stop.quote_timestamp == datetime(2026, 2, 17, 21, 0, tzinfo=UTC)


def test_a_stop_the_open_gaps_through_funds_an_earlier_named_entry():
    """bar_event_priority's gapped-leg branch. ZM holds a bracket whose stop
    Tuesday's open gaps through; AAPL's entry, sized that morning at 12
    shares, fills at the same open after a gap up. Sorted by name AAPL would
    go first: the $1,278 left over buys 11 at $108, and the twelfth share
    expires. The stop sells first, so the entry gets all 12."""
    data = {"ZM": [_bar("2026-03-02", 100, 102, 99, 101),
                   _bar("2026-03-03", 80, 82, 78, 81)],
            "AAPL": [_bar("2026-03-02", 100, 101, 99, 100),
                     _bar("2026-03-03", 108, 108.5, 107, 108)]}
    emu = _emulator()
    _enter(emu, _at("2026-03-02"), symbol="ZM", cash=8_750.0)
    [zm_buy] = _tick(emu, data, _at("2026-03-03"))
    assert (zm_buy.symbol, zm_buy.incremental_quantity) == ("ZM", 87.0)
    assert 11 * 108.0 < emu.spendable_cash() < 12 * 108.0
    _enter(emu, _at("2026-03-03"))
    stop, buy = _tick(emu, data, _at("2026-03-04"))
    assert (stop.symbol, stop.source.split(":")[0]) == ("ZM", "bracket_sl_gap")
    assert (buy.symbol, buy.side) == ("AAPL", "buy")
    assert stop.quote_timestamp == buy.quote_timestamp == datetime(
        2026, 3, 3, 14, 30, tzinfo=UTC)
    assert buy.incremental_quantity == 12.0
    assert emu.get_execution_summary()["next_open_expired_order_count"] == 0
    assert emu.get_positions() == {"AAPL": 12.0}
