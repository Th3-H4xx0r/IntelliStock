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
