#!/usr/bin/env python3
"""Offline expectation per backtest window, from the real StrategyX code.

The engine and this replay should broadly agree. Where they do not, the engine
is measuring something else — its fee model applies a 45.6 bps microcap spread
to SPY/TQQQ, and it credits SPY dividends while crediting TQQQ none — so this
gives a reference to argue against rather than a number to match exactly.
"""
import sys
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from strategies.strategy_x import StrategyX  # noqa: E402

COST_BPS = 2.0

WINDOWS = {
    "bull_2026": ("2026-04-01", "2026-06-01"),
    "bear_2026": ("2026-02-01", "2026-04-01"),
    "year_2025": ("2025-01-01", "2026-01-01"),
    "bear_2022": ("2022-01-01", "2023-01-01"),
    "chop_2015": ("2015-01-01", "2016-01-01"),
    "year_2024": ("2024-01-01", "2025-01-01"),
    "covid_2020": ("2020-01-01", "2021-01-01"),
}

CFG = {
    "strategy_x_enabled": True,
    "core_bull_symbol": "TQQQ", "core_chop_symbol": "SPY",
    "core_bear_symbol": "", "core_weight": 0.9, "core_band_pct": 0.05,
    "core_filter_symbol": "QQQ", "core_filter_ma_bars": 200,
    "core_vol_bars": 20, "core_vol_gate_mult": 2.25,
    "core_vol_median_bars": 252, "core_vol_median_min_samples": 60,
    "satellite_pct": 0.0, "min_order_usd": 50.0, "cost_haircut_pct": 0.006,
}


class Emu:
    def __init__(self, cash):
        self._cash = float(cash)
        self._positions = {}

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or {}
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())

    def buy(self, sym, amt, price):
        amt = min(amt, self._cash)
        if amt <= 0 or price <= 0:
            return
        fee = amt * COST_BPS / 1e4
        self._cash -= amt
        self._positions[sym] = self._positions.get(sym, 0.0) + (amt - fee) / price

    def sell(self, sym, frac, price):
        qty = self._positions.get(sym, 0.0) * max(0.0, min(1.0, frac))
        if qty <= 0 or price <= 0:
            return
        gross = qty * price
        self._cash += gross - gross * COST_BPS / 1e4
        rest = self._positions.get(sym, 0.0) - qty
        if rest <= 1e-9:
            self._positions.pop(sym, None)
        else:
            self._positions[sym] = rest


def run_window(px, start, end):
    hist = px[px.index < start]
    win = px[(px.index >= start) & (px.index < end)]
    if len(win) < 5:
        return None
    strat, emu, cache = StrategyX(), Emu(10_000.0), {}
    bars = [{"t": (t.replace(tzinfo=timezone.utc)).isoformat(), "c": float(c)}
            for t, c in hist["QQQ"].items()]
    eq, pending, legs, prev = [], None, 0, None
    for ts, row in win.iterrows():
        tsu = ts.replace(tzinfo=timezone.utc)
        prices = {s: float(row[s]) for s in ("QQQ", "TQQQ", "SPY")}
        if pending:
            out, sizes = pending
            for s, d in out.items():
                if not s.startswith("_") and d == -1:
                    emu.sell(s, sizes.get(s, {}).get("sell_fraction", 1.0), prices[s])
            for s, d in out.items():
                if not s.startswith("_") and d == 1:
                    emu.buy(s, sizes.get(s, {}).get("buy_cash", 0.0), prices[s])
            pending = None
        bars.append({"t": tsu.isoformat(), "c": float(row["QQQ"])})
        out = strat.run_once(["TQQQ", "SPY"], prices, tsu, CFG, {},
                             data={"QQQ": {"bars": bars}},
                             portfolio_emulator=emu, strategy_cache=cache)
        if out:
            pending = (dict(out), out.get("_nexus_position_sizes", {}))
        leg = "TQQQ" if emu._positions.get("TQQQ", 0) > 0 else "SPY"
        if prev and leg != prev:
            legs += 1
        prev = leg
        eq.append(emu.get_portfolio_value(prices))
    s = pd.Series(eq, index=win.index)
    ret = (s.iloc[-1] / s.iloc[0] - 1) * 100
    dd = (s / s.cummax() - 1).min() * 100
    return {
        "return_%": ret, "maxdd_%": dd, "legs": legs,
        "spy_%": (win["SPY"].iloc[-1] / win["SPY"].iloc[0] - 1) * 100,
        "tqqq_%": (win["TQQQ"].iloc[-1] / win["TQQQ"].iloc[0] - 1) * 100,
        "bars": len(win),
    }


def main():
    px = yf.download(["QQQ", "TQQQ", "SPY"], start="2009-01-01",
                     auto_adjust=True, progress=False)["Close"].dropna()
    rows = []
    for name, (a, b) in WINDOWS.items():
        r = run_window(px, a, b)
        if r:
            rows.append({"window": name, **r})
    df = pd.DataFrame(rows).set_index("window")
    df["vs_spy"] = df["return_%"] - df["spy_%"]
    print("OFFLINE EXPECTATION PER WINDOW (real StrategyX code, 2bps, next-bar fills)")
    print("=" * 92)
    print(df.to_string(float_format=lambda x: f"{x:>9.2f}"))


if __name__ == "__main__":
    main()
