#!/usr/bin/env python3
"""Strategy XS over fifteen years, driven through the REAL run_once.

Same convention as `strategy_x_bear_regime_matrix.py`: the actual
`StrategyXS.run_once` bar by bar through a minimal emulator, with the
strategy's own point-in-time filter and next-bar fills.

Two deliberate differences from the Strategy X harness:

  * CALENDAR YEARS are the primary output, not window slices. Fifteen slices
    flatter a strategy because some of them are six weeks long, and the natural
    unit for "makes money in every regime" is the year.
  * The four-part gate is FROZEN here, in code, before any run. It was written
    into the plan before the implementation existed precisely so that it cannot
    be moved after seeing the numbers.

    python3 scripts/strategy_xs_matrix.py                 # the frozen config
    python3 scripts/strategy_xs_matrix.py k=v k=v ...     # overrides
    SX_COST_BPS=5 python3 scripts/strategy_xs_matrix.py   # cost sensitivity
"""
import json
import os
import sys
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from strategies.strategy_xs import StrategyXS  # noqa: E402
from strategy_xs import DEFAULTS  # noqa: E402

#: CALIBRATED TO THE ENGINE, not assumed. 2 bps is fine for a strategy that
#: changes legs five times a year; at this turnover it made the Strategy X
#: harness overstate return twofold — local +147.6% against the engine's
#: +67.55% on the identical window. Measuring BT406990's own fills against its
#: own benchmark quotes gives SPY +23.04 bps with mean == median to 2dp, which
#: is a FLAT modelled spread rather than sampling noise.
COST_BPS = float(os.environ.get("SX_COST_BPS", "23"))
BAR_WINDOW = 400

UNIVERSE = ["QQQ", "TQQQ", "QLD", "BIL", "GLD", "UUP", "DBMF", "SPY",
            "SQQQ", "PSQ"]


class Emu:
    """Minimal PortfolioEmulator stand-in: cash + share counts, cost on fills."""

    def __init__(self, cash):
        self._cash = float(cash)
        self._positions = {}
        self.traded = 0.0

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or {}
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())

    def buy(self, sym, cash_amount, price):
        cash_amount = min(cash_amount, self._cash)
        if cash_amount <= 0 or price <= 0:
            return
        fee = cash_amount * COST_BPS / 1e4
        self.traded += cash_amount
        self._cash -= cash_amount
        self._positions[sym] = self._positions.get(sym, 0.0) + (cash_amount - fee) / price

    def sell(self, sym, fraction, price):
        qty = self._positions.get(sym, 0.0) * max(0.0, min(1.0, fraction))
        if qty <= 0 or price <= 0:
            return
        proceeds = qty * price
        self.traded += proceeds
        self._cash += proceeds - proceeds * COST_BPS / 1e4
        left = self._positions.get(sym, 0.0) - qty
        if left <= 1e-9:
            self._positions.pop(sym, None)
        else:
            self._positions[sym] = left


def load_prices(start="2010-01-01"):
    cache = Path(os.environ.get("XS_PRICE_CACHE", "/tmp/strategy_xs_prices.pkl"))
    if cache.exists():
        frame = pd.read_pickle(cache)
        if set(UNIVERSE) <= set(frame.columns):
            return frame
    frame = yf.download(UNIVERSE, start=start, auto_adjust=True,
                        progress=False)["Close"]
    frame = frame[frame["QQQ"].notna()]
    frame.to_pickle(cache)
    return frame


def replay(frame, cfg):
    strat = StrategyXS()
    emu = Emu(100_000.0)
    cache, bars = {}, {sym: [] for sym in UNIVERSE}
    equity, dates, orders = [], [], 0
    pending = None
    watchlist = [s for s in UNIVERSE if s != "QQQ"]

    for ts, row in frame.iterrows():
        ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        prices = {s: float(row[s]) for s in UNIVERSE
                  if pd.notna(row[s]) and float(row[s]) > 0}

        # Fill what the PREVIOUS bar decided, at THIS bar's price. Filling on
        # the close the decision saw is a real lookahead.
        if pending:
            out, sizes = pending
            for sym, dec in out.items():
                if not sym.startswith("_") and dec == -1 and sym in prices:
                    emu.sell(sym, sizes.get(sym, {}).get("sell_fraction", 1.0),
                             prices[sym])
            for sym, dec in out.items():
                if not sym.startswith("_") and dec == 1 and sym in prices:
                    emu.buy(sym, sizes.get(sym, {}).get("buy_cash", 0.0),
                            prices[sym])
            pending = None

        for sym in UNIVERSE:
            if sym in prices:
                bars[sym].append({"t": ts_utc.isoformat(), "c": prices[sym]})
                if len(bars[sym]) > BAR_WINDOW:
                    del bars[sym][0]

        out = strat.run_once(
            watchlist, prices, ts_utc, cfg, {},
            data={s: {"bars": bars[s]} for s in UNIVERSE if bars[s]},
            portfolio_emulator=emu, strategy_cache=cache, mode="backtest")
        if out:
            orders += len([s for s in out if not s.startswith("_")])
            pending = (dict(out), (out or {}).get("_nexus_position_sizes", {}))

        equity.append(emu.get_portfolio_value(prices))
        dates.append(ts)

    curve = pd.Series(equity, index=pd.DatetimeIndex(dates))
    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-9)
    return curve, {"orders": orders,
                   "turnover_pct_yr": emu.traded / max(curve.mean(), 1e-9)
                   / years * 100}


def cagr(curve):
    norm = curve / curve.iloc[0]
    years = (norm.index[-1] - norm.index[0]).days / 365.25
    return (norm.iloc[-1] ** (1 / years) - 1) * 100


def maxdd(curve):
    norm = curve / curve.iloc[0]
    return (norm / norm.cummax() - 1).min() * 100


def sharpe(curve):
    rets = curve.pct_change().dropna()
    return rets.mean() / rets.std() * np.sqrt(252) if rets.std() else 0.0


def yearly(curve):
    annual = curve.resample("YE").last().pct_change()
    annual.iloc[0] = curve.resample("YE").last().iloc[0] / curve.iloc[0] - 1
    return annual * 100


def halves(curve):
    mid = curve.index[len(curve) // 2]
    return curve[curve.index <= mid], curve[curve.index >= mid]


def main():
    overrides = {}
    for arg in sys.argv[1:]:
        key, _, value = arg.partition("=")
        if value.lower() in ("true", "false"):
            overrides[key] = value.lower() == "true"
        else:
            try:
                overrides[key] = float(value) if "." in value else int(value)
            except ValueError:
                overrides[key] = value

    frame = load_prices()
    cfg = {**DEFAULTS, "strategy_xs_enabled": True, **overrides}
    # Measured once the filter and the volatility median both exist.
    warm = DEFAULTS["core_filter_ma_bars"] + DEFAULTS["core_vol_median_bars"]

    curve, stats = replay(frame, cfg)
    xs = curve.iloc[warm:]
    spy = frame["SPY"].loc[xs.index]

    print("=" * 78)
    print(f"STRATEGY XS  {xs.index[0].date()} -> {xs.index[-1].date()}"
          f"   cost {COST_BPS:.0f} bps"
          + (f"   overrides: {overrides}" if overrides else ""))
    print("=" * 78)
    print(f"{'':<10}{'CAGR':>9}{'maxDD':>9}{'Sharpe':>8}{'turnover':>11}")
    print(f"{'XS':<10}{cagr(xs):>8.2f}%{maxdd(xs):>8.2f}%{sharpe(xs):>8.2f}"
          f"{stats['turnover_pct_yr']:>10.0f}%")
    print(f"{'SPY':<10}{cagr(spy):>8.2f}%{maxdd(spy):>8.2f}%{sharpe(spy):>8.2f}"
          f"{0:>10.0f}%")

    xs_yr, spy_yr = yearly(xs), yearly(spy)
    print(f"\n{'year':<6}{'XS':>9}{'SPY':>9}   verdict")
    for i, stamp in enumerate(xs_yr.index):
        x, b = xs_yr.iloc[i], spy_yr.iloc[i]
        mark = []
        if x < 0:
            mark.append("negative")
        if x < b:
            mark.append("below SPY")
        print(f"{stamp.year:<6}{x:>8.1f}%{b:>8.1f}%   "
              + (", ".join(mark) if mark else "ok"))

    # ── the frozen gate, written before the implementation existed ──
    xs_h, spy_h = halves(xs), halves(spy)
    checks = {
        "CAGR above SPY": cagr(xs) > cagr(spy),
        "maxDD better than SPY": abs(maxdd(xs)) < abs(maxdd(spy)),
        "no more losing years than SPY":
            int((xs_yr < 0).sum()) <= int((spy_yr < 0).sum()),
        "halves agree in sign": all(
            cagr(a) > cagr(b) and abs(maxdd(a)) < abs(maxdd(b))
            for a, b in zip(xs_h, spy_h)),
    }
    print("\nFROZEN GATE")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{'GATE PASSED' if all(checks.values()) else 'GATE FAILED'}"
          f" — {int((xs_yr < 0).sum())} losing years against SPY's "
          f"{int((spy_yr < 0).sum())}, "
          f"{int((xs_yr.values < spy_yr.values).sum())} of {len(xs_yr)} below SPY")

    dest = os.environ.get("XS_MATRIX_OUT", "")
    if dest:
        Path(dest).write_text(json.dumps({
            "cost_bps": COST_BPS, "overrides": overrides,
            "cagr": cagr(xs), "maxdd": maxdd(xs), "sharpe": sharpe(xs),
            "turnover_pct_yr": stats["turnover_pct_yr"],
            "years": {str(s.year): v for s, v in xs_yr.items()},
            "spy_years": {str(s.year): v for s, v in spy_yr.items()},
            "gate": checks, "passed": all(checks.values()),
        }, indent=2, default=float))


if __name__ == "__main__":
    main()
