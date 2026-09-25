"""backtest_bar_events: hint plumbing and the bar collector (spec 6.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import backtest_bar_events as bbe  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from swing_port_calendar_fixtures import (  # noqa: E402
    AVAILABLE,
    OPENS,
    daily_bar as _daily,
)

UTC = timezone.utc


# -- execution_hint_kwargs ---------------------------------------------------

@pytest.mark.parametrize("hint, decision", [
    ({"buy_cash": 1234.5}, 1),                    # EB / outlier / index core
    ({"sell_fraction": 0.4}, -1),
    ({}, 1),
    (None, 1),
    ("not-a-dict", 1),
    ({"buy_cash": 10.0, "fill_at_next_open": False, "whole_shares": False,
      "bracket": None}, 1),
    ({"fill_at_next_open": "true"}, 1),           # only the literal True
    ({"fill_at_next_open": True}, 0),             # a hold places nothing
])
def test_hints_that_set_nothing_add_nothing(hint, decision):
    assert bbe.execution_hint_kwargs(hint, decision) == {}


def test_a_swing_entry_carries_all_three():
    hint = {"buy_cash": 1250.0, "whole_shares": True, "fill_at_next_open": True,
            "bracket": {"take_profit_price": 218.0, "stop_loss_price": 188.0}}
    assert bbe.execution_hint_kwargs(hint, 1) == {
        "fill_at_next_open": True, "whole_shares": True,
        "bracket": {"take_profit_price": 218.0, "stop_loss_price": 188.0}}


def test_a_swing_exit_carries_only_the_next_open_flag():
    hint = {"sell_fraction": 1.0, "fill_at_next_open": True,
            "whole_shares": True,
            "bracket": {"take_profit_price": 2.0, "stop_loss_price": 1.0}}
    assert bbe.execution_hint_kwargs(hint, -1) == {"fill_at_next_open": True}


def test_a_malformed_bracket_is_loud():
    with pytest.raises(ValueError, match="mapping"):
        bbe.execution_hint_kwargs({"bracket": [218.0, 188.0]}, 1)


# -- bar open times ----------------------------------------------------------

def test_a_daily_bar_opens_at_the_nyse_open_across_dst():
    resolve = bbe.make_bar_open_resolver(
        interval=timedelta(days=1),
        session_open_resolver=bbe.equity_daily_session_open)
    assert resolve({"t": "2026-03-06T05:00:00Z"}) == datetime(
        2026, 3, 6, 14, 30, tzinfo=UTC)
    assert resolve({"t": "2026-03-09T04:00:00Z"}) == datetime(
        2026, 3, 9, 13, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="session open"):
        resolve({"t": "2026-02-16T05:00:00Z"})     # Presidents' Day


def test_an_intraday_bar_opens_at_its_label():
    resolve = bbe.make_bar_open_resolver(interval=timedelta(minutes=15))
    assert resolve({"t": "2026-03-02T15:45:00Z"}) == datetime(
        2026, 3, 2, 15, 45, tzinfo=UTC)


# -- collect_bar_events ------------------------------------------------------

def _collect(data, requirements, clock):
    bbe.reset_label_cache()
    return bbe.collect_bar_events(
        data, requirements, clock, bar_time_to_datetime=bar_time_to_datetime,
        bar_available_at=AVAILABLE, bar_open_at=OPENS)


def test_every_bar_since_the_requirement_arrives_oldest_first():
    """Friday, then the holiday Monday has no bar, then Tuesday: a tick on
    Wednesday sees both, not just the latest."""
    data = {"AAPL": [_daily("2026-02-12"), _daily("2026-02-13", o=101.0),
                     _daily("2026-02-17", o=102.0),
                     _daily("2026-02-18", o=103.0)]}
    since = datetime(2026, 2, 13, 13, 0, tzinfo=UTC)
    clock = datetime(2026, 2, 18, 13, 0, tzinfo=UTC)
    rows = _collect(data, {"AAPL": since}, clock)["AAPL"]
    assert [r["o"] for r in rows] == [101.0, 102.0]
    assert rows[0]["bar_ts"] == datetime(2026, 2, 13, 14, 30, tzinfo=UTC)
    assert rows[0]["available_at"] == datetime(2026, 2, 13, 21, 0, tzinfo=UTC)
    assert rows[1]["t"] == "2026-02-17T05:00:00Z"


def test_a_bar_that_opened_before_the_requirement_is_left_out():
    data = {"AAPL": [_daily("2026-03-02"), _daily("2026-03-03", o=104.0)]}
    since = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)      # after Monday's open
    clock = datetime(2026, 3, 4, 13, 0, tzinfo=UTC)
    assert [r["o"] for r in _collect(data, {"AAPL": since}, clock)["AAPL"]] == [
        104.0]


def test_a_symbol_with_no_new_bar_is_absent():
    data = {"AAPL": [_daily("2026-03-02")], "HALT": []}
    clock = datetime(2026, 3, 3, 13, 0, tzinfo=UTC)
    since = datetime(2026, 3, 3, 13, 0, tzinfo=UTC)
    assert _collect(data, {"AAPL": since, "HALT": since, "GONE": since},
                    clock) == {}


def test_a_malformed_bar_is_skipped_not_fatal():
    data = {"AAPL": [_daily("2026-03-02"),
                     {"t": "2026-03-03T05:00:00Z", "o": "x", "h": 1, "l": 1,
                      "c": 1},
                     _daily("2026-03-04", o=105.0)]}
    since = datetime(2026, 3, 2, 13, 0, tzinfo=UTC)
    clock = datetime(2026, 3, 5, 13, 0, tzinfo=UTC)
    assert [r["o"] for r in _collect(data, {"AAPL": since}, clock)["AAPL"]] == [
        100.0, 105.0]


def test_labels_are_reparsed_when_the_bar_list_grows():
    bars = [_daily("2026-03-02")]
    data = {"AAPL": bars}
    since = datetime(2026, 3, 2, 13, 0, tzinfo=UTC)
    bbe.reset_label_cache()
    kwargs = dict(bar_time_to_datetime=bar_time_to_datetime,
                  bar_available_at=AVAILABLE, bar_open_at=OPENS)
    clock = datetime(2026, 3, 4, 13, 0, tzinfo=UTC)
    assert len(bbe.collect_bar_events(data, {"AAPL": since}, clock,
                                      **kwargs)["AAPL"]) == 1
    bars.append(_daily("2026-03-03", o=104.0))
    assert len(bbe.collect_bar_events(data, {"AAPL": since}, clock,
                                      **kwargs)["AAPL"]) == 2


# -- FW-bt minor 1: a malformed bar never aborts the run ---------------------

def _bar(date, o, h, l, c):
    return {"t": f"{date}T05:00:00Z", "o": o, "h": h, "l": l, "c": c, "v": 1}


@pytest.mark.parametrize("bad, defect", [
    (_bar("2026-03-03", 100.0, 99.0, 101.0, 100.0), "high"),      # high < low
    (_bar("2026-03-03", float("nan"), 101.0, 99.0, 100.0), "open"),
    (_bar("2026-03-03", 100.0, 101.0, 99.0, float("inf")), "close"),
    (_bar("2026-03-03", 100.0, 101.0, 0.0, 100.0), "low"),
    (_bar("2026-03-03", -5.0, 101.0, 99.0, 100.0), "open"),
])
def test_a_bar_the_simulator_would_refuse_is_skipped_with_one_warning(
        monkeypatch, bad, defect):
    warned = []
    monkeypatch.setattr(bbe, "_warn", warned.append)
    data = {"AAPL": [_daily("2026-03-02"), bad, _daily("2026-03-04", o=105.0)]}
    since = datetime(2026, 3, 2, 13, 0, tzinfo=UTC)
    clock = datetime(2026, 3, 5, 13, 0, tzinfo=UTC)
    rows = _collect(data, {"AAPL": since}, clock)["AAPL"]
    assert [r["t"][:10] for r in rows] == ["2026-03-02", "2026-03-04"]
    assert len(warned) == 1
    assert "AAPL" in warned[0] and "2026-03-03" in warned[0] and defect in warned[0]
    # The next tick re-reads the same bar: skipped again, warned once only.
    bbe.collect_bar_events(data, {"AAPL": since}, clock,
                           bar_time_to_datetime=bar_time_to_datetime,
                           bar_available_at=AVAILABLE, bar_open_at=OPENS)
    assert len(warned) == 1


def test_a_malformed_bar_leaves_the_order_pending_across_ticks_then_the_next_bar_fills(
        monkeypatch):
    """backtest-review M1 (probe_bad_bar.py): a high<low bar for a symbol
    with a next-open bracket order raised in process_bar_events on every
    tick, which aborted the run. It is now skipped, and the order fills at
    the first sound open after it."""
    from portfolio_emulator import PortfolioEmulator
    from simulated_execution import (LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
                                     NextEventExecutionSimulator)

    monkeypatch.setattr(bbe, "_warn", lambda message: None)
    PortfolioEmulator._PASSIVE_OVERRIDE = None
    emu = PortfolioEmulator(10_000.0, execution_simulator=NextEventExecutionSimulator(
        LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL), execution_delay=timedelta(days=1))
    emu.execute_signal("AAA", 1, 100.0, timestamp=datetime(2026, 6, 2, 12, 0),
                       cash_per_trade=1_000.0, order_source="main_signal",
                       whole_shares=True, fill_at_next_open=True,
                       bracket={"take_profit_price": 109.0, "stop_loss_price": 94.0})
    bad = {"t": "2026-06-02T04:00:00Z", "o": 100.0, "h": 99.0, "l": 101.0, "c": 100.0}
    data = {"AAA": [bad]}
    bbe.reset_label_cache()
    for day in (3, 4):
        now = datetime(2026, 6, day, 12, 0, tzinfo=UTC)
        bars = bbe.collect_bar_events(data, emu.bar_event_requirements(), now,
                                      bar_time_to_datetime=bar_time_to_datetime,
                                      bar_available_at=AVAILABLE, bar_open_at=OPENS)
        assert bars == {}
        assert emu.process_bar_events(bars, now) == []
        assert emu.pending_execution_symbols() == ("AAA",)
    data["AAA"].append({"t": "2026-06-04T04:00:00Z", "o": 101.0, "h": 102.0,
                        "l": 100.0, "c": 101.5})
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    bars = bbe.collect_bar_events(data, emu.bar_event_requirements(), now,
                                  bar_time_to_datetime=bar_time_to_datetime,
                                  bar_available_at=AVAILABLE, bar_open_at=OPENS)
    [fill] = emu.process_bar_events(bars, now)
    assert (fill.symbol, fill.side, fill.cumulative_quantity) == ("AAA", "buy", 9.0)
    assert emu.has_bracket_legs()
