"""IV snapshots and IV Rank (ST iv_collector.py), stored in SwingIvSnapshots."""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pandas as pd
import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import iv as ivc  # noqa: E402

TODAY = date(2026, 6, 1)


def _mock_ticker(spot=100.0, call_iv=0.30, put_iv=0.34, expiries=("2026-07-02",)):
    t = MagicMock()
    t.options = expiries
    t.history.return_value = pd.DataFrame({"Close": [spot]})
    calls = pd.DataFrame({"strike": [95.0, 100.0, 105.0],
                          "impliedVolatility": [0.5, call_iv, 0.5]})
    puts = pd.DataFrame({"strike": [95.0, 100.0, 105.0],
                         "impliedVolatility": [0.5, put_iv, 0.5]})
    t.option_chain.return_value = NS(calls=calls, puts=puts)
    return t


def test_snapshot_iv_averages_atm_call_and_put(monkeypatch):
    monkeypatch.setattr(ivc, "yf", NS(Ticker=lambda s: _mock_ticker(call_iv=0.30, put_iv=0.34)))
    assert ivc.snapshot_iv("AAPL", today=TODAY) == 0.32


def test_snapshot_iv_returns_none_without_expiries_or_iv(monkeypatch):
    t = MagicMock()
    t.options = ()
    monkeypatch.setattr(ivc, "yf", NS(Ticker=lambda s: t))
    assert ivc.snapshot_iv("AAPL", today=TODAY) is None
    t2 = _mock_ticker()
    nan_df = pd.DataFrame({"strike": [100.0], "impliedVolatility": [float("nan")]})
    t2.option_chain.return_value = NS(calls=nan_df, puts=nan_df)
    monkeypatch.setattr(ivc, "yf", NS(Ticker=lambda s: t2))
    assert ivc.snapshot_iv("AAPL", today=TODAY) is None


class OptAdapter:
    def get_option_contracts(self, symbol, **kw):
        self.kw = kw
        out = []
        for exp in ("2026-06-26", "2026-07-02", "2026-07-10"):
            for strike in (95.0, 100.0, 105.0):
                for kind in ("call", "put"):
                    out.append(NS(symbol=f"{kind}-{exp}-{strike}", underlying=symbol,
                                  option_type=kind, strike=strike, expiration=exp))
        return out

    def get_option_snapshots(self, symbols):
        self.requested = list(symbols)
        return {s: NS(iv=0.28 if s.startswith("call") else 0.32) for s in symbols}


def test_snapshot_iv_alpaca_takes_the_nearest_30_dte_atm_pair():
    a = OptAdapter()
    assert ivc.snapshot_iv_alpaca("AAPL", adapter=a, spot=100.4, today=TODAY) == 0.3
    assert sorted(a.requested) == ["call-2026-07-02-100.0", "put-2026-07-02-100.0"]
    assert a.kw["strike_gte"] == round(100.4 * 0.93, 2)
    assert ivc.snapshot_iv_alpaca("AAPL", adapter=a, spot=None, today=TODAY) is None


def test_run_iv_snapshot_writes_one_row_per_symbol(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: 0.25)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.27)
    out = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 100.0,
                              today=TODAY, symbols=["AAPL", "MSFT"])
    assert out["recorded"] == ["AAPL", "MSFT"] and out["complete"] is True
    row = store.get("SwingIvSnapshots", "AAPL|2026-06-01")
    assert (row["iv30"], row["iv30_alpaca"], row["date"]) == (0.25, 0.27, "2026-06-01")


def test_run_iv_snapshot_idempotent_same_day(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: 0.25)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.27)
    ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0, today=TODAY,
                        symbols=["AAPL"])
    again = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0,
                                today=TODAY, symbols=["AAPL"])
    assert again["recorded"] == [] and again["skipped"] == ["AAPL"]


def test_one_leg_is_enough_and_none_is_a_failure(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: None)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.31 if s == "AAPL" else None)
    out = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0, today=TODAY,
                              symbols=["AAPL", "MSFT"])
    assert out["recorded"] == ["AAPL"] and out["failed"] == ["MSFT"]
    assert store.get("SwingIvSnapshots", "AAPL|2026-06-01")["iv30"] is None
    assert store.get("SwingIvSnapshots", "MSFT|2026-06-01") is None


def test_a_failing_spot_read_blanks_only_the_alpaca_leg(store, monkeypatch):
    # ST fetched the spot inside snapshot_iv_alpaca's try: a failure there cost
    # that leg, never the run.
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: 0.25)
    seen = []
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca",
                        lambda s, **kw: seen.append(kw["spot"]) or 0.27)

    def spot_for(symbol):
        if symbol == "AAPL":
            raise RuntimeError("bars endpoint down")
        return 100.0

    out = ivc.run_iv_snapshot(store, adapter=object(), spot_for=spot_for, today=TODAY,
                              symbols=["AAPL", "MSFT"])
    assert out["recorded"] == ["AAPL", "MSFT"] and out["complete"] is True
    assert seen == [None, 100.0]


def test_the_budget_stops_the_run_early(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: 0.2)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.2)
    ticks = iter([100.0, 100.0, 100.0])
    out = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0, today=TODAY,
                              symbols=["AAPL", "MSFT"], deadline=105.0,
                              now_fn=lambda: next(ticks))
    assert out["complete"] is False and out["recorded"] == []


def _history(store, symbol, ivs):
    store.insert("SwingIvSnapshots", [
        {"id": f"{symbol}|2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "symbol": symbol,
         "date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "iv30": iv,
         "iv30_alpaca": None} for i, iv in enumerate(ivs)], conflict="replace")


def test_iv_rank_computed_from_trailing_window(store):
    _history(store, "AAPL", [0.20 + (0.20 * i / 98) for i in range(99)] + [0.30])
    assert ivc.load_iv_rank(store, "AAPL") == 50.0


def test_iv_rank_none_below_min_rows_degenerate_or_missing(store):
    _history(store, "AAPL", [0.25] * (ivc.MIN_RANK_ROWS - 1))
    assert ivc.load_iv_rank(store, "AAPL") is None
    _history(store, "MSFT", [0.25] * 100)
    assert ivc.load_iv_rank(store, "MSFT") is None
    assert ivc.load_iv_rank(store, "NOPE") is None
