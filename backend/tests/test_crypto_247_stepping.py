"""Crypto backtests must step 24/7 — the equity NYSE/PT session gate (which
skips overnight and weekends) must NOT apply to crypto. Pure helper lives in
broker_session so it's testable without importing broker.py."""

import datetime

from broker_session import advance_backtest_time

_INC = datetime.timedelta(minutes=15)


def _within(ct):                       # dummy equity session: 5..20 weekdays
    return ct.weekday() < 5 and 5 <= ct.hour < 20


def _next_open(ct):                    # dummy "next market open"
    return datetime.datetime(ct.year, ct.month, ct.day) + datetime.timedelta(days=1, hours=5)


def test_crypto_advances_by_raw_increment_outside_equity_hours():
    ct = datetime.datetime(2026, 4, 17, 23, 0, 0)   # Fri 11pm (outside session)
    assert advance_backtest_time(ct, _INC, True, _within, _next_open) == ct + _INC


def test_crypto_advances_over_weekend():
    ct = datetime.datetime(2026, 4, 18, 3, 0, 0)     # Saturday
    assert advance_backtest_time(ct, _INC, True, _within, _next_open) == ct + _INC


def test_equity_skips_to_next_open_outside_session():
    ct = datetime.datetime(2026, 4, 17, 23, 0, 0)
    assert advance_backtest_time(ct, _INC, False, _within, _next_open) == _next_open(ct)


def test_equity_advances_inside_session():
    ct = datetime.datetime(2026, 4, 17, 10, 0, 0)     # Fri 10am (in session)
    assert advance_backtest_time(ct, _INC, False, _within, _next_open) == ct + _INC


def test_none_next_open_falls_back_to_increment():
    ct = datetime.datetime(2026, 4, 18, 3, 0, 0)
    assert advance_backtest_time(ct, _INC, False, _within, lambda _c: None) == ct + _INC
