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


# -- FW-bt minor 2: a next-open order never waits more than 5 sessions --------

def _day_tick(emu, data, now, prices=None):
    """One broker tick: pending closes and the bar hook, then the end-of-tick
    snapshot that moves the emulator's clock (save_portfolio_snapshot)."""
    fills = _tick(emu, data, now)
    emu.save_portfolio_snapshot(dict(prices or {}), now)
    return fills


def test_a_next_open_order_whose_symbol_never_prints_again_is_cancelled_after_5_sessions(
        monkeypatch):
    """backtest-review M2: a delisting, an acquisition or a data gap left the
    order pending for the rest of the run -- a slot and a sector held, and
    12.5% of equity reserved away from every later buy."""
    warned = []
    monkeypatch.setattr(bbe, "_warn", warned.append)
    emu = _emulator()
    assert _enter(emu, _at("2026-03-02"), symbol="GONE")
    emu.save_portfolio_snapshot({}, _at("2026-03-02"))
    assert emu.get_buying_power(sum(emu._execution_cash_reservations.values())) < 10_000.0
    data = {"GONE": [_bar("2026-02-27", 100, 101, 99, 100)]}
    # Mon-Fri 03-02..03-06 are the 5 sessions after the decision. The tick
    # after the fifth has closed still waits (its clock is the last tick's).
    for day in ("2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"):
        _day_tick(emu, data, _at(day))
        assert emu.pending_execution_symbols() == ("GONE",), day
    assert warned == []
    _day_tick(emu, data, _at("2026-03-10"))
    assert emu.pending_execution_symbols() == ()
    assert emu._execution_cash_reservations == {}
    assert emu.get_buying_power() == 10_000.0
    assert emu.has_next_open_orders() is False
    summary = emu._execution_simulator.execution_summary()
    assert summary["next_open_order_count"] == 1
    assert summary["next_open_expired_order_count"] == 1
    assert summary["unfilled_order_count"] == 0
    [line] = warned
    assert "GONE" in line and "5 sessions" in line


def test_a_halt_shorter_than_5_sessions_still_fills_at_the_reopen():
    """T7's halt case stands: four sessions without a bar, then the reopen."""
    emu = _emulator()
    _enter(emu, _at("2026-03-02"))
    data = {"AAPL": [_bar("2026-02-27", 100, 101, 99, 100)]}
    for day in ("2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"):
        assert _day_tick(emu, data, _at(day)) == []
    data["AAPL"].append(_bar("2026-03-06", 97, 99, 96, 98))       # Fri reopens
    [buy] = _day_tick(emu, data, _at("2026-03-09"))
    assert (buy.side, buy.quote_timestamp) == (
        "buy", datetime(2026, 3, 6, 14, 30, tzinfo=UTC))
    assert emu.has_bracket_legs() is True


def test_a_next_open_sell_of_a_name_that_stops_printing_is_cancelled_too(monkeypatch):
    monkeypatch.setattr(bbe, "_warn", lambda message: None)
    emu = _emulator()
    emu._positions = {"GONE": 10.0}
    assert emu.execute_signal("GONE", -1, 100.0, timestamp=_at("2026-03-02"),
                              sell_fraction=1.0, order_source="main_signal",
                              fill_at_next_open=True)
    emu.save_portfolio_snapshot({}, _at("2026-03-02"))
    for day in ("2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09",
                "2026-03-10"):
        _day_tick(emu, {}, _at(day))
    assert emu.pending_execution_symbols() == ()
    assert emu._execution_position_reservations == {}
    assert emu.get_positions() == {"GONE": 10.0}


def test_sessions_are_counted_on_the_nyse_calendar():
    after = datetime(2026, 2, 13, 13, 0, tzinfo=UTC)         # Fri 08:00 ET
    # Fri 02-13, then Presidents' Day, then Tue-Fri 02-17..02-20.
    assert bbe.completed_sessions_after(after, datetime(2026, 2, 20, 20, 0, tzinfo=UTC)) == 4
    assert bbe.completed_sessions_after(after, datetime(2026, 2, 20, 21, 0, tzinfo=UTC)) == 5
    assert bbe.completed_sessions_after(after, after) == 0
    # A decision after the open does not count that session.
    assert bbe.completed_sessions_after(
        datetime(2026, 2, 13, 15, 0, tzinfo=UTC), datetime(2026, 2, 13, 22, 0, tzinfo=UTC)) == 0


def test_without_the_calendar_library_sessions_are_weekdays(monkeypatch):
    import live_calendar

    monkeypatch.setattr(live_calendar, "_HAS_LIB", False)
    after = datetime(2026, 2, 13, 13, 0, tzinfo=UTC)
    # Presidents' Day counts as a weekday session in the fallback.
    assert bbe.completed_sessions_after(after, datetime(2026, 2, 19, 21, 0, tzinfo=UTC)) == 5
    assert bbe.completed_sessions_after(after, datetime(2026, 2, 19, 20, 0, tzinfo=UTC)) == 4
