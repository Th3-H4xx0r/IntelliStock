#!/usr/bin/env python3
"""Replay the REAL StrategyX code over real bars and check it against the study.

`_strategy_x_final.py` computes the expected behaviour with vectorised pandas.
This drives `StrategyX.run_once` — the actual shipped code path, with the actual
point-in-time filter and the actual order sizing — bar by bar through a minimal
emulator. If the two disagree, the implementation is wrong, not the study.

This is the check unit tests cannot give: it exercises pit_daily_closes,
core_signal, plan_targets and targets_to_orders together on 15 years of real
data, and it is the only place the strategy's own arithmetic meets a price
series it did not choose.
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


class Emu:
    """Minimal PortfolioEmulator stand-in: cash + share counts, cost on fills."""

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

    def buy(self, sym, cash_amount, price):
        cash_amount = min(cash_amount, self._cash)
        if cash_amount <= 0 or price <= 0:
            return
        fee = cash_amount * COST_BPS / 1e4
        qty = (cash_amount - fee) / price
        self._cash -= cash_amount
        self._positions[sym] = self._positions.get(sym, 0.0) + qty

    def sell(self, sym, fraction, price):
        qty = self._positions.get(sym, 0.0) * max(0.0, min(1.0, fraction))
        if qty <= 0 or price <= 0:
            return
        proceeds = qty * price
        self._cash += proceeds - proceeds * COST_BPS / 1e4
        left = self._positions.get(sym, 0.0) - qty
        if left <= 1e-9:
            self._positions.pop(sym, None)
        else:
            self._positions[sym] = left


def main():
    cfg = {
        "strategy_x_enabled": True,
        "core_bull_symbol": "TQQQ", "core_chop_symbol": "SPY",
        "core_bear_symbol": "", "core_weight": 0.9, "core_band_pct": 0.05,
        "core_filter_symbol": "QQQ", "core_filter_ma_bars": 200,
        "core_vol_bars": 20, "core_vol_gate_mult": 1.2,
        "core_vol_median_bars": 252, "core_vol_median_min_samples": 60,
        "satellite_pct": 0.0, "min_order_usd": 50.0, "cost_haircut_pct": 0.006,
    }
    for arg in sys.argv[1:]:
        k, _, v = arg.partition("=")
        if v.lower() in ("true", "false"):
            cfg[k] = v.lower() == "true"
        else:
            try:
                cfg[k] = float(v) if "." in v else int(v)
            except ValueError:
                cfg[k] = v

    px = yf.download(["QQQ", "TQQQ", "SPY"], start="2010-01-01",
                     auto_adjust=True, progress=False)["Close"].dropna()
    print(f"replaying {len(px)} sessions {px.index[0].date()} -> "
          f"{px.index[-1].date()}  cfg: core_weight={cfg['core_weight']} "
          f"vol_gate={cfg['core_vol_gate_mult']} bear='{cfg['core_bear_symbol']}'")

    strat = StrategyX()
    emu = Emu(100_000.0)
    cache = {}
    equity, dates, flips = [], [], 0
    prev_leg = None
    # Bars are handed to the strategy exactly as the engine does: a growing list
    # of {"t", "c"} dicts. The strategy applies its OWN point-in-time cutoff.
    bars = []
    pending = None          # decided on bar t, filled on bar t+1
    for i, (ts, row) in enumerate(px.iterrows()):
        ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        prices = {s: float(row[s]) for s in ("QQQ", "TQQQ", "SPY")}

        # FILL FIRST, at THIS bar's price, what the PREVIOUS bar decided. The
        # engine is next-event ("a sell submitted while the 15:00 bar is
        # processed fills at the 16:00 quote"), so filling at the close the
        # decision was made on would be a lookahead worth several points a year.
        if pending:
            out, sizes = pending
            for sym, dec in out.items():
                if not sym.startswith("_") and dec == -1:
                    emu.sell(sym, sizes.get(sym, {}).get("sell_fraction", 1.0),
                             prices[sym])
            for sym, dec in out.items():
                if not sym.startswith("_") and dec == 1:
                    emu.buy(sym, sizes.get(sym, {}).get("buy_cash", 0.0),
                            prices[sym])
            pending = None

        bars.append({"t": ts_utc.isoformat(), "c": float(row["QQQ"])})
        out = strat.run_once(["TQQQ", "SPY"], prices, ts_utc, cfg, {},
                             data={"QQQ": {"bars": bars}},
                             portfolio_emulator=emu, strategy_cache=cache)
        if out:
            pending = (dict(out), (out or {}).get("_nexus_position_sizes", {}))

        leg = "TQQQ" if emu._positions.get("TQQQ", 0) > 0 else "SPY"
        if prev_leg and leg != prev_leg:
            flips += 1
        prev_leg = leg
        equity.append(emu.get_portfolio_value(prices))
        dates.append(ts)

    eq = pd.Series(equity, index=pd.DatetimeIndex(dates))
    # Drop the warmup: until the MA and the vol median exist the strategy holds
    # flat by design, and including that stretch flatters or penalises nothing
    # but the start date.
    warm = max(cfg["core_filter_ma_bars"],
               cfg["core_vol_bars"] + cfg["core_vol_median_min_samples"])
    eq = eq[eq.index >= eq.index[warm]]
    win = px.loc[eq.index]

    def stat(curve, label):
        c = curve / curve.iloc[0]
        yrs_ = (c.index[-1] - c.index[0]).days / 365.25
        rr = c.pct_change().dropna()
        yy = c.resample("YE").last().pct_change().dropna() * 100
        return {
            "strategy": label,
            "cagr": (c.iloc[-1] ** (1 / yrs_) - 1) * 100,
            "maxdd": (c / c.cummax() - 1).min() * 100,
            "sharpe": rr.mean() / rr.std() * np.sqrt(252),
            "mult": c.iloc[-1],
            "n100": int((yy >= 100).sum()),
        }, yy

    rows = []
    s_x, yr = stat(eq, "strategy_x (shipped code)")
    rows.append(s_x)
    # Benchmarks under the IDENTICAL window and fill convention.
    for sym in ("TQQQ", "SPY"):
        b, _ = stat(win[sym], f"{sym} buy & hold")
        rows.append(b)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25

    print("\n" + "=" * 78)
    print(f"REPLAY OF THE SHIPPED CODE  {eq.index[0].date()} -> "
          f"{eq.index[-1].date()} ({yrs:.1f}y)")
    print("=" * 78)
    print(pd.DataFrame(rows).set_index("strategy")
          .to_string(float_format=lambda x: f"{x:>9.2f}"))
    print(f"\n  leg changes {flips} ({flips/yrs:.1f}/yr)")
    print("\n  strategy_x year by year (%)")
    for y, v in yr.items():
        print(f"    {y.year}  {v:>8.1f}")


if __name__ == "__main__":
    main()
