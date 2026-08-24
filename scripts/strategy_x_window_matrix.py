#!/usr/bin/env python3
"""The requested 60/20/20 allocation, tested over rolling 2-month windows.

    60%  TQQQ (risk-on) or SQQQ (risk-off)   core_weight=1.0 of a 0.6 budget
    20%  trend ETFs                          commodity_pct=0.20
    20%  stocks from graph nexus             satellite_pct=0.20

Driven through the REAL StrategyX class, bar by bar, next-bar fills, with the
strategy's own point-in-time filter. The stock sleeve is fed a SYNTHETIC
conviction map because the live graph score is saturated (3 distinct values over
506,498 trade contexts) and has no measured cross-sectional IC — so two
brackets are reported:

    stocks=momentum   a BEST CASE: the sleeve ranks on 60d momentum, i.e. it
                      pretends the graph had real skill
    stocks=spy        a NEUTRAL case: the sleeve just holds SPY

The truth for the real graph sleeve is at or below the neutral case. If the
design only clears SPY in the best case, it does not clear it.

Goal under test: beat SPY in EVERY 2-month window, by a lot.
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
COMMOD = ["GLD", "SLV", "USO", "UNG", "GDX", "XLE", "DBA", "CPER"]
STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "JPM",
          "XOM", "UNH", "COST", "HD"]
CORE = ["QQQ", "TQQQ", "SQQQ", "SPY"]

CFG = {
    "strategy_x_enabled": True,
    "core_bull_symbol": "TQQQ",
    "core_chop_symbol": "SPY",
    "core_bear_symbol": "SQQQ",       # ENABLED as requested
    "core_weight": 1.0,               # 1.0 of a 0.6 budget = 60% of NAV
    "core_band_pct": 0.05,
    "core_filter_symbol": "QQQ",
    "core_filter_ma_bars": 200,
    "core_vol_bars": 20,
    "core_vol_gate_mult": 2.25,
    "core_vol_median_bars": 252,
    "core_vol_median_min_samples": 60,
    "commodity_pct": 0.20,
    "commodity_symbols": COMMOD,
    "commodity_max_names": 2,
    "satellite_pct": 0.20,
    "satellite_max_names": 4,
    "min_order_usd": 25.0,
    "cost_haircut_pct": 0.006,
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
        return self._cash + sum(q * float(px.get(s, 0.0) or 0.0)
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


def run_window(px, start, end, stock_mode):
    hist = px[px.index < start]
    win = px[(px.index >= start) & (px.index < end)]
    if len(win) < 15 or len(hist) < 260:
        return None
    strat, emu, cache = StrategyX(), Emu(10_000.0), {}
    bars = {s: [{"t": t.replace(tzinfo=timezone.utc).isoformat(), "c": float(c)}
                for t, c in hist[s].items() if c == c]
            for s in px.columns}
    eq, pending = [], None
    mom = px.pct_change(60)

    for ts, row in win.iterrows():
        tsu = ts.replace(tzinfo=timezone.utc)
        prices = {s: float(row[s]) for s in px.columns if row[s] == row[s]}
        if pending:
            out, sizes = pending
            for s, d in out.items():
                if not s.startswith("_") and d == -1 and s in prices:
                    emu.sell(s, sizes.get(s, {}).get("sell_fraction", 1.0),
                             prices[s])
            for s, d in out.items():
                if not s.startswith("_") and d == 1 and s in prices:
                    emu.buy(s, sizes.get(s, {}).get("buy_cash", 0.0), prices[s])
            pending = None

        for s in px.columns:
            if row[s] == row[s]:
                bars[s].append({"t": tsu.isoformat(), "c": float(row[s])})

        data = {s: {"bars": bars[s]} for s in px.columns}
        if stock_mode == "momentum":
            m = mom.loc[ts, STOCKS].dropna()
            data["conviction_scores"] = {s: float(v) for s, v in m.items()}
        else:                                     # neutral: sleeve holds SPY
            data["conviction_scores"] = {}

        cfg = dict(CFG)
        if stock_mode == "shipped":
            # CONTROL: the config actually on main — 90% core, no SQQQ, no
            # sleeves. Same windows, same fills, same costs.
            cfg.update({"satellite_pct": 0.0, "commodity_pct": 0.0,
                        "core_weight": 0.9, "core_bear_symbol": ""})
        elif stock_mode == "no_sqqq":
            # 60/20/20 but risk-off routes to SPY instead of shorting.
            cfg["core_bear_symbol"] = ""
        elif stock_mode != "momentum":
            cfg["satellite_pct"] = 0.0
            cfg["commodity_pct"] = 0.20
            cfg["core_weight"] = 0.75   # keep core at 60% of NAV: 0.8*0.75=0.6

        out = strat.run_once(list(px.columns), prices, tsu, cfg, {}, data=data,
                             portfolio_emulator=emu, strategy_cache=cache)
        if out:
            pending = (dict(out), out.get("_nexus_position_sizes", {}))
        eq.append(emu.get_portfolio_value(prices))

    s = pd.Series(eq, index=win.index)
    ret = (s.iloc[-1] / s.iloc[0] - 1) * 100
    spy = (win["SPY"].iloc[-1] / win["SPY"].iloc[0] - 1) * 100
    qqq_r = (win["QQQ"].iloc[-1] / win["QQQ"].iloc[0] - 1) * 100
    dd = (s / s.cummax() - 1).min() * 100
    return {"ret": ret, "spy": spy, "vs_spy": ret - spy, "maxdd": dd,
            "regime": "bull" if qqq_r > 4 else ("bear" if qqq_r < -4 else "chop")}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "momentum"
    px = yf.download(CORE + COMMOD + STOCKS, start="2011-01-01",
                     auto_adjust=True, progress=False)["Close"]
    px = px.dropna(subset=["QQQ", "TQQQ", "SQQQ", "SPY"])
    print(f"stock sleeve mode: {mode}   data {px.index[0].date()} -> "
          f"{px.index[-1].date()}\n")

    # rolling 2-month windows, stepped 2 months
    starts = pd.date_range("2013-01-01", "2026-06-01", freq="2MS")
    rows = []
    for a in starts:
        b = a + pd.DateOffset(months=2)
        r = run_window(px, a, b, mode)
        if r:
            rows.append({"window": a.strftime("%Y-%m"), **r})

    df = pd.DataFrame(rows)
    print(f"{'window':<9}{'regime':<7}{'strat_%':>9}{'spy_%':>8}{'vs_spy':>9}{'maxdd':>8}")
    print("-" * 50)
    for _, r in df.iterrows():
        print(f"{r['window']:<9}{r['regime']:<7}{r['ret']:>9.2f}{r['spy']:>8.2f}"
              f"{r['vs_spy']:>+9.2f}{r['maxdd']:>8.1f}")

    print("\n" + "=" * 62)
    n = len(df)
    beat = int((df["vs_spy"] > 0).sum())
    big = int((df["vs_spy"] > 5).sum())
    print(f"windows: {n} | beat SPY: {beat} ({beat/n*100:.0f}%) | "
          f"beat by >5pp: {big} ({big/n*100:.0f}%)")
    print(f"mean vs SPY {df['vs_spy'].mean():+.2f}pp | "
          f"median {df['vs_spy'].median():+.2f}pp | "
          f"worst {df['vs_spy'].min():+.2f}pp | best {df['vs_spy'].max():+.2f}pp")
    print("\nby regime:")
    print(df.groupby("regime").agg(
        n=("vs_spy", "size"), beat=("vs_spy", lambda s: int((s > 0).sum())),
        mean_vs_spy=("vs_spy", "mean"), worst=("vs_spy", "min")
    ).to_string(float_format=lambda x: f"{x:>8.2f}"))
    comp = (1 + df["ret"] / 100).prod()
    comp_spy = (1 + df["spy"] / 100).prod()
    print(f"\ncompounded across all windows: strategy {(comp-1)*100:+.1f}%  "
          f"vs SPY {(comp_spy-1)*100:+.1f}%")


if __name__ == "__main__":
    main()
