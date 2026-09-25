"""Wheel screening and expiry, pinned to ST wheel_trader.py, plus ST's
tests/test_wheel_scan_guard.py ported to the cache marker."""
import importlib.util
import os
import sys
import types
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import wheel_rules  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")


def _st():
    spec = importlib.util.spec_from_file_location(
        "_swing_st_wheel", os.path.join(_ST_DIR, "wheel_trader_pure.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(seed, n=90, drift=0.001, vol=0.025):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    high = close * (1 + rng.uniform(0.005, 0.03, n))
    low = close * (1 - rng.uniform(0.005, 0.03, n))
    idx = pd.date_range("2026-02-02", periods=n, freq="B")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1_000_000, 3_000_000, n).astype(float)},
                        index=idx)


def _fake_yf(earnings, calls):
    today = date.today()

    class _T:
        def __init__(self, symbol):
            calls.append(symbol)
            self.symbol = symbol

        @property
        def calendar(self):
            days = earnings.get(self.symbol)
            if days is None:
                return {}
            return {"Earnings Date": [pd.Timestamp(today + timedelta(days=days))]}

    return types.SimpleNamespace(Ticker=_T)


def test_get_candidates_matches_st(monkeypatch):
    st = _st()
    raw = {f"S{i:02d}": _frame(i) for i in range(40)}
    earnings = {"S02": 3, "S07": 10}
    st.yf = _fake_yf(earnings, [])
    st.next_friday = lambda: "2026-06-12"
    ours_calls = []
    monkeypatch.setattr(wheel_rules, "yf", _fake_yf(earnings, ours_calls))
    theirs = st.get_candidates(raw, list(raw))
    ours = wheel_rules.get_candidates(raw, list(raw), expiry="2026-06-12")
    assert ours == theirs
    assert [c["symbol"] for c in ours][:1] == ["S07"]    # S02 dropped by earnings
    assert "S02" not in {c["symbol"] for c in ours}
    # Earnings are looked up only for names that passed every other filter.
    assert len(ours_calls) < len(raw)


def test_config_thresholds_are_honoured(monkeypatch):
    monkeypatch.setattr(wheel_rules, "yf", _fake_yf({}, []))
    raw = {f"S{i:02d}": _frame(i) for i in range(40)}
    base = wheel_rules.get_candidates(raw, list(raw), expiry="2026-06-12")
    tight = wheel_rules.get_candidates(raw, list(raw), expiry="2026-06-12",
                                       cfg={"rsi_max": 50})
    assert len(tight) < len(base)
    assert all(c["rsi"] <= 50 for c in tight)


def test_short_or_missing_history_is_skipped():
    raw = {"SHORT": _frame(1, n=40), "OK": _frame(2)}
    pre = wheel_rules.screen_technicals(raw, ["SHORT", "MISSING", "OK"])
    assert {p["symbol"] for p in pre} <= {"OK"}


def test_apply_earnings_blocks_within_seven_days_inclusive():
    pre = {"symbol": "X", "stock_price": 10.0, "strike_price": 9.5, "otm_pct": 5.0,
           "est_premium": 0.1, "est_premium_pct": 1.0, "rsi": 45.0, "sma50": 9.0,
           "atr": 0.4, "atr_pct": 4.0}
    assert wheel_rules.apply_earnings(pre, 7, expiry="2026-06-12") is None
    c = wheel_rules.apply_earnings(pre, 8, expiry="2026-06-12")
    assert c["expiry"] == "2026-06-12" and c["earnings_days"] == 8
    assert wheel_rules.apply_earnings(pre, None, expiry="2026-06-12")["earnings_days"] is None


def test_etf_and_flaky_names_skip_the_earnings_call(monkeypatch):
    calls = []
    monkeypatch.setattr(wheel_rules, "yf", _fake_yf({"SPY": 1, "NVDA": 1}, calls))
    assert wheel_rules.wheel_earnings_days("SPY") is None
    assert wheel_rules.wheel_earnings_days("NVDA") is None
    assert calls == []


def _calendar_client(sessions):
    class _Client:
        def get_calendar(self, req):
            return [types.SimpleNamespace(date=d) for d in sorted(sessions)
                    if req.start <= d <= req.end]
    return _Client()


def _weekdays(start, end, holidays=()):
    out, d = set(), start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            out.add(d)
        d += timedelta(days=1)
    return out


def test_next_friday_matches_st_across_a_good_friday(monkeypatch):
    st = _st()
    good_friday = date(2026, 4, 3)
    for holidays in ((good_friday,), (good_friday, date(2026, 4, 2))):
        sessions = _weekdays(date(2026, 3, 1), date(2026, 6, 30), holidays)
        st._get_trading_client = lambda: _calendar_client(sessions)
        day = date(2026, 3, 16)
        while day <= date(2026, 4, 17):
            frozen = day

            class _Frozen(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(frozen.year, frozen.month, frozen.day, 10, 30, tzinfo=tz)

            monkeypatch.setattr(st, "datetime", _Frozen)
            ours = wheel_rules.next_friday(
                day, lambda a, b: {d for d in sessions if a <= d <= b})
            assert ours == st.next_friday(), day
            day += timedelta(days=1)


def test_next_friday_known_answers_and_fallback():
    sessions = _weekdays(date(2026, 3, 1), date(2026, 6, 30), (date(2026, 4, 3),))
    cal = lambda a, b: {d for d in sessions if a <= d <= b}  # noqa: E731
    assert wheel_rules.next_friday(date(2026, 3, 23), cal) == "2026-04-02"   # Monday
    assert wheel_rules.next_friday(date(2026, 6, 1), cal) == "2026-06-12"    # Monday, 11 DTE

    def broken(a, b):
        raise RuntimeError("calendar down")

    assert wheel_rules.next_friday(date(2026, 3, 23), broken) == "2026-04-03"


def test_sector_filter_caps_mapped_sectors_and_passes_unknown():
    cands = [{"symbol": s} for s in ("AAPL", "MSFT", "NVDA", "JPM", "ZZ1", "ZZ2", "ZZ3")]
    kept = [c["symbol"] for c in wheel_rules.sector_filter(cands, max_per_sector=2)]
    assert kept == ["AAPL", "MSFT", "JPM", "ZZ1", "ZZ2", "ZZ3"]


# -- ST tests/test_wheel_scan_guard.py, on the strategy_cache marker ---------

def test_no_marker_returns_false():
    assert wheel_rules.scan_already_ran_this_week(None, date(2026, 6, 2)) is False


def test_marker_from_this_week_returns_true():
    assert wheel_rules.scan_already_ran_this_week("2026-06-01", date(2026, 6, 2)) is True


def test_marker_from_last_week_returns_false():
    assert wheel_rules.scan_already_ran_this_week("2026-05-26", date(2026, 6, 2)) is False


def test_corrupt_marker_fails_open():
    assert wheel_rules.scan_already_ran_this_week("not-a-timestamp", date(2026, 6, 2)) is False


def test_occ_parts_reads_the_fixed_width_suffix():
    assert wheel_rules.occ_parts("APH261002P00130000") == ("APH", "2026-10-02", "put", 130.0)
    assert wheel_rules.occ_parts("BRKB261002C00480500") == ("BRKB", "2026-10-02", "call", 480.5)
    assert wheel_rules.occ_parts("AAPL") is None
    assert wheel_rules.occ_parts("APH261002X00130000") is None


def test_occ_parts_refuses_an_impossible_expiry_date():
    # Task 21 turns this expiry into a date; month 13 must not get that far.
    assert wheel_rules.occ_parts("APH261302P00130000") is None
    assert wheel_rules.occ_parts("APH260230P00130000") is None
