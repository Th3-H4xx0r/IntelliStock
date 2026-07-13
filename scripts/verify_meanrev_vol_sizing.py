#!/usr/bin/env python3
"""Faithful vol-vs-equal check for the crypto MeanRev strategy.

Runs ``strategies.crypto.meanrev.Meanrev`` through the REAL ``PortfolioEmulator``
(Binance.US 0.02% taker) over a cached OHLCV bars file, once with ``sizing="equal"``
and once with ``sizing="vol"``, on the target + out-of-sample windows. Asserts
``vol >= equal - tolerance`` on every window (regression guard for the sizing change).

Usage:
    python3 scripts/verify_meanrev_vol_sizing.py --cache path/to/bars_cache.json

Cache format: {"universe": [sym...], "times": [iso...],
               "bars": {sym: [[t,o,h,l,c,v], ...]}}
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from strategies.crypto.meanrev import Meanrev  # noqa: E402

FEE = 0.0002
CASH = 10_000.0
WARMUP = 220
WINDOW = 260


def run(bars, times, uni, sizing, lo, hi):
    pe = PortfolioEmulator(initial_cash=CASH, taker_fee=FEE)
    strat = Meanrev()
    cfg = {"band": "low", "top_k": 2, "regime_ma": 200, "rsi_buy": 35,
           "rsi_exit": 55, "sizing": sizing, "allocations": []}
    for i in range(lo, hi):
        w0 = max(0, i - WINDOW + 1)
        data = {s: bars[s][w0:i + 1] for s in uni}
        prices = {s: bars[s][i]["c"] for s in uni}
        ct = dt.datetime.strptime(times[i][:19], "%Y-%m-%dT%H:%M:%S")
        res = strat.run_once(symbols=list(uni), prices=prices, current_time=ct,
                             config=cfg, conditions={}, data=data,
                             portfolio_emulator=pe, mode="LIVE")
        if not isinstance(res, dict):
            continue
        sizes = res.get("_nexus_position_sizes")
        sizes = sizes if isinstance(sizes, dict) else {}
        sig = {k: v for k, v in res.items() if isinstance(k, str) and not k.startswith("_")}
        for s, v in sig.items():
            if v == -1:
                pe.execute_signal(s, -1, prices[s], timestamp=ct, sell_fraction=1.0)
        for s, v in sig.items():
            if v == 1:
                sz = sizes.get(s)
                cash = float(sz["buy_cash"]) if isinstance(sz, dict) and "buy_cash" in sz \
                    else pe.get_portfolio_value(prices) / 2.0
                pe.execute_signal(s, 1, prices[s], timestamp=ct, cash_per_trade=cash)
        pe.save_portfolio_snapshot(prices, timestamp=ct)
    final = pe.get_portfolio_value({s: bars[s][hi - 1]["c"] for s in uni})
    return (final / CASH - 1) * 100


def idx(times, iso):
    for i, t in enumerate(times):
        if t >= iso:
            return i
    return len(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    args = ap.parse_args()
    d = json.load(open(args.cache))
    uni, times = d["universe"], d["times"]
    bars = {s: [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                for r in d["bars"][s]] for s in uni}
    windows = {"tgt 26-04..07": ("2026-04-13", "2026-07-13"),
               "oos 25-06..26-04": ("2025-06-08", "2026-04-13")}
    ok = True
    print(f"universe={uni}")
    print(f"{'window':20s} {'equal':>8s} {'vol':>8s} {'delta':>7s}")
    for name, (s0, e0) in windows.items():
        lo = max(idx(times, s0 + "T00:00:00Z"), WARMUP)
        hi = idx(times, e0 + "T00:00:00Z")
        if hi - lo < 50:
            print(f"{name:20s}  [too few bars, skipped]")
            continue
        eq = run(bars, times, uni, "equal", lo, hi)
        vo = run(bars, times, uni, "vol", lo, hi)
        print(f"{name:20s} {eq:+8.2f} {vo:+8.2f} {vo - eq:+7.2f}")
        if vo < eq - 0.5:
            ok = False
    print("PASS" if ok else "FAIL: vol regressed below equal")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
