#!/usr/bin/env python3
"""Every named regime window, from ONE continuous replay of the shipped code.

Same convention as `strategy_x_replay.py` and `strategy_x_window_matrix.py`:
the REAL `StrategyX.run_once` is driven bar by bar through a minimal emulator,
with the strategy's own point-in-time filter and next-bar fills. The difference
here is that the whole bear universe is priceable — BIL, the managed-futures
ETFs, SQQQ, the commodity sleeve — so the bear overlay can actually allocate,
and windows are sliced out of ONE continuous run rather than re-warmed per
window. That matches how the API run is read: state at a window's open is
whatever the preceding tape produced, not a cold start.

This is a research reference, not the verdict. The engine models dividends and
a much heavier spread; `docs/superpowers/research/2026-08-26-strategy-x-bear-
results.md` is the authority. Use this to choose a candidate, then spend an
API run on it.

    python3 scripts/strategy_x_bear_regime_matrix.py                # baseline vs active
    python3 scripts/strategy_x_bear_regime_matrix.py k=v k=v ...    # config overrides
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

from strategies.strategy_x import StrategyX  # noqa: E402

COST_BPS = 2.0
#: Only the trailing slice is handed to the strategy. `pit_daily_closes` walks
#: every bar it is given, so an ever-growing list makes the replay quadratic.
#: 400 covers the longest lookback in play (252 vol-median samples + 20 vol
#: bars = 272) with room to spare.
BAR_WINDOW = 400

CORE = ["QQQ", "TQQQ", "SPY", "SQQQ"]
BEAR = ["BIL", "DBMF", "KMLM", "CTA"]
COMMOD = ["GLD", "SLV", "USO", "UNG", "GDX", "XLE", "DBA", "CPER"]
UNIVERSE = CORE + BEAR + COMMOD

#: The windows the results report names, plus the two the report could not
#: reach. Each is [start, end).
WINDOWS = {
    "2015 chop":            ("2015-01-01", "2016-01-01"),
    "2018 Q4 selloff":      ("2018-10-01", "2019-01-01"),
    "2020 covid crash":     ("2020-02-14", "2020-04-01"),
    "2020 covid recovery":  ("2020-04-01", "2020-09-01"),
    "2021 bull":            ("2021-01-01", "2022-01-01"),
    "2022 full year":       ("2022-01-01", "2023-01-01"),
    "2022 H1 selloff":      ("2022-01-01", "2022-07-01"),
    "2022 summer recovery": ("2022-07-01", "2022-08-16"),
    "2022 Q3 selloff":      ("2022-08-16", "2022-10-01"),
    "2022 Q4":              ("2022-10-01", "2023-01-01"),
    "2023 recovery":        ("2023-01-01", "2024-01-01"),
    "2024 bull":            ("2024-01-01", "2025-01-01"),
    "2025 spring drawdown": ("2025-02-19", "2025-04-08"),
    "2025 spring recovery": ("2025-04-08", "2025-07-01"),
    "2026 H1":              ("2026-01-01", "2026-07-01"),
}

#: Windows the objective judges as bear. Everything else must merely not be
#: harmed, and must stay positive.
BEAR_WINDOWS = ("2018 Q4 selloff", "2020 covid crash", "2022 full year",
                "2022 H1 selloff", "2022 Q3 selloff", "2025 spring drawdown")

BASE_CFG = {
    "strategy_x_enabled": True,
    "core_bull_symbol": "TQQQ",
    "core_chop_symbol": "SPY",
    "core_bear_symbol": "",
    "core_weight": 1.0,
    "core_band_pct": 0.05,
    "core_filter_symbol": "QQQ",
    "core_filter_ma_bars": 200,
    "core_vol_bars": 20,
    "core_vol_gate_mult": 2.25,
    "core_vol_median_bars": 252,
    "core_vol_median_min_samples": 60,
    # 15 / 15 / 70 — the operator's allocation. The stock sleeve is declared
    # but unfilled here, because the historical Graph ranking is current-state
    # biased and ~5 minutes per simulated day; its 15% routes to unlevered SPY,
    # which is the honest stand-in for a sleeve whose skill is unproven.
    "satellite_pct": 0.15,
    "commodity_pct": 0.15,
    "commodity_symbols": COMMOD,
    "commodity_max_names": 2,
    "commodity_mom_bars": 60,
    "commodity_trend_bars": 100,
    "bear_system_mode": "off",
    "bear_cash_symbol": "BIL",
    "crisis_alpha_symbols": ["DBMF", "KMLM", "CTA"],
    "crisis_alpha_pct": 0.50,
    "crisis_alpha_min_history_bars": 60,
    "bear_kicker_symbol": "SQQQ",
    "bear_kicker_pct": 0.05,
    "min_order_usd": 50.0,
    "cost_haircut_pct": 0.006,
}



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
        self._cash -= cash_amount
        self._positions[sym] = self._positions.get(sym, 0.0) + (cash_amount - fee) / price

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


def load_prices(start="2010-01-01"):
    cache = Path(os.environ.get("SX_PRICE_CACHE",
                                "/tmp/strategy_x_regime_prices.pkl"))
    if cache.exists():
        frame = pd.read_pickle(cache)
        if set(UNIVERSE) <= set(frame.columns):
            return frame
    frame = yf.download(UNIVERSE, start=start, auto_adjust=True,
                        progress=False)["Close"]
    frame = frame[frame["QQQ"].notna()]
    frame.to_pickle(cache)
    return frame


def replay(frame, cfg, label):
    """Drive the shipped code over `frame`, returning its daily equity curve."""
    strat = StrategyX()
    emu = Emu(100_000.0)
    cache, bars = {}, {sym: [] for sym in UNIVERSE}
    equity, dates, orders = [], [], 0
    pending = None
    watchlist = [s for s in UNIVERSE if s != "QQQ"]

    for ts, row in frame.iterrows():
        ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        prices = {s: float(row[s]) for s in UNIVERSE
                  if pd.notna(row[s]) and float(row[s]) > 0}

        # Fill what the PREVIOUS bar decided, at THIS bar's price. The engine is
        # next-event; filling on the close the decision saw is a real lookahead.
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
            portfolio_emulator=emu, strategy_cache=cache, mode="backtest",
        )
        if out:
            decided = {s: d for s, d in out.items() if not s.startswith("_")}
            orders += len(decided)
            pending = (dict(out), (out or {}).get("_nexus_position_sizes", {}))

        equity.append(emu.get_portfolio_value(prices))
        dates.append(ts)

    curve = pd.Series(equity, index=pd.DatetimeIndex(dates), name=label)
    return curve, orders


def window_stats(curve, start, end):
    """Return (%, maxDD%) over [start, end) using the last close before start."""
    idx = curve.index
    before = idx[idx < pd.Timestamp(start)]
    lo = before[-1] if len(before) else idx[0]
    seg = curve[(idx >= lo) & (idx < pd.Timestamp(end))]
    if len(seg) < 2:
        return float("nan"), float("nan")
    norm = seg / seg.iloc[0]
    return (norm.iloc[-1] - 1) * 100, (norm / norm.cummax() - 1).min() * 100


def full_stats(curve):
    norm = curve / curve.iloc[0]
    years = (norm.index[-1] - norm.index[0]).days / 365.25
    rets = norm.pct_change().dropna()
    return {
        "cagr": (norm.iloc[-1] ** (1 / years) - 1) * 100,
        "maxdd": (norm / norm.cummax() - 1).min() * 100,
        "sharpe": rets.mean() / rets.std() * np.sqrt(252) if rets.std() else 0.0,
        "mult": norm.iloc[-1],
    }


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
    # The measured span starts once the MA200 and the vol median both exist.
    warm = BASE_CFG["core_filter_ma_bars"] + BASE_CFG["core_vol_median_bars"]

    arms = {
        "baseline": {**BASE_CFG, "bear_system_mode": "off"},
        "active": {**BASE_CFG, "bear_system_mode": "active",
                   "bear_regime_enabled": True,
                   "bear_regime_fast_ma_bars": 20,
                   "bear_regime_mid_ma_bars": 50,
                   "bear_regime_confirm_bars": 2,
                   "bear_regime_transition_risk_fraction": 0.55,
                   **overrides},
    }
    curves, order_counts = {}, {}
    # The baseline arm never varies while tuning the ladder, so it is computed
    # once and reused. Deleting the file re-derives it.
    baseline_cache = Path("/tmp/strategy_x_baseline_curve.pkl")
    for name, cfg in arms.items():
        if name == "baseline" and baseline_cache.exists():
            curves[name] = pd.read_pickle(baseline_cache)
            order_counts[name] = -1
            continue
        curve, orders = replay(frame, cfg, name)
        curves[name] = curve.iloc[warm:]
        order_counts[name] = orders
        if name == "baseline":
            curves[name].to_pickle(baseline_cache)
    spy = frame["SPY"].loc[curves["baseline"].index]
    curves["SPY"] = spy

    rows = []
    for label, (start, end) in WINDOWS.items():
        row = {"window": label, "bear": label in BEAR_WINDOWS}
        for name in ("baseline", "active", "SPY"):
            ret, dd = window_stats(curves[name], start, end)
            row[name] = ret
            row[f"{name}_dd"] = dd
        rows.append(row)
    table = pd.DataFrame(rows).set_index("window")

    print("=" * 96)
    print(f"REGIME MATRIX  {curves['baseline'].index[0].date()} -> "
          f"{curves['baseline'].index[-1].date()}"
          + (f"   overrides: {overrides}" if overrides else ""))
    print("=" * 96)
    print(table[["bear", "baseline", "active", "SPY", "active_dd", "SPY_dd"]]
          .to_string(float_format=lambda x: f"{x:>9.2f}"))

    print("\nfull period")
    for name in ("baseline", "active", "SPY"):
        stats = full_stats(curves[name])
        extra = (f"  orders {order_counts[name]}" if name in order_counts else "")
        print(f"  {name:<9} CAGR {stats['cagr']:>7.2f}  maxDD {stats['maxdd']:>7.2f}"
              f"  Sharpe {stats['sharpe']:>5.2f}  x{stats['mult']:>7.2f}{extra}")

    # Two gates, reported separately because they answer different questions.
    # OBJECTIVE is what was actually asked for: make money in every regime and
    # beat SPY by a lot. NO-HARM is the stricter "never worse than the current
    # baseline in any window" diagnostic — a bar that any protective feature
    # fails by construction, since insurance costs something in a bull. Keeping
    # them apart stops a deliberate, priced trade-off from being read as a
    # defect, and stops a failure of the real objective from being hidden.
    print("\ngate (OBJECTIVE: positive everywhere; bear windows beat SPY)")
    failures, harmed = [], []
    for label in table.index:
        row = table.loc[label]
        checks = [("positive", row["active"] > 0)]
        if row["bear"]:
            checks.append(("beats SPY", row["active"] > row["SPY"]))
        bad = [name for name, ok in checks if not ok]
        if bad:
            failures.append(label)
        if row["active"] < row["baseline"] - 1e-9:
            harmed.append(label)
        print(f"  {label:<22} {row['active']:>8.2f}  "
              + ("PASS" if not bad else "FAIL " + ", ".join(bad)))
    active, spy_stats = full_stats(curves["active"]), full_stats(curves["SPY"])
    margin = active["cagr"] - spy_stats["cagr"]
    print(f"  {'full-period CAGR margin':<22} {margin:>8.2f}  "
          + ("PASS" if margin >= 5 else "FAIL needs >= 5pp"))
    print(f"\n{len(failures)} objective failure(s)"
          + (": " + ", ".join(failures) if failures else ""))
    print(f"{len(harmed)} window(s) below the no-harm baseline"
          + (": " + ", ".join(harmed) if harmed else ""))

    dest = os.environ.get("SX_MATRIX_OUT", "")
    if dest:
        Path(dest).write_text(json.dumps(
            {"overrides": overrides,
             "windows": table.reset_index().to_dict("records"),
             "full": {n: full_stats(curves[n]) for n in curves},
             "failures": failures}, indent=2, default=float))


if __name__ == "__main__":
    main()
