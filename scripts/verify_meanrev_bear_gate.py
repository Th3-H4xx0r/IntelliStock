#!/usr/bin/env python3
"""Faithful bear-gate check for the crypto MeanRev strategy.

Runs the REAL strategies.crypto.meanrev.Meanrev through the REAL
PortfolioEmulator at Binance.US fees (0.02%), base config vs
``bear_gate_ma=1200`` (50d of hourly bars), across 9 named regime intervals
2021-2026. Win rule per interval: bull (EW B&H > 0) -> strategy >= B&H;
bear (B&H <= 0) -> strategy > 0.

Verified 2026-07-14 (this script, hourly caches):
    window       B&H      base    gated
    2021bull   +190.0   +64.78   +48.39
    2022bear    -67.1   -19.30   +11.23   <- the win the gate exists for
    2023recov   +57.8   +17.75   +17.22
    2324bull   +119.1   +30.64   +23.21
    2024chop    -35.2    +1.67    +6.52
    late24      +74.3   -13.33   -11.15
    OOS         -36.4    +8.84   +11.75
    tgt         -20.8   +13.40    +1.14   <- the cost: mild-bear scalps shrink
    fullrec     -50.0   +25.79   +13.03
    wins: base 4/9, gated 5/9 (all 5 bears positive; robust across
    gate windows 1200-1680h and hysteresis 0-0.05 in the fast harness)

Bar caches: hourly OHLCV JSON {"universe": [...], "times": [...],
"bars": {sym: [[iso,o,h,l,c,v], ...]}} fetched from Alpaca's public
v1beta3 crypto endpoint. Point MEGA_CACHE / REC8_CACHE env vars at them
(defaults match the job-tmp layout this was developed in).
"""
import os
import sys
import json
import datetime as dt

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "backend"))
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from strategies.crypto.meanrev import Meanrev  # noqa: E402

FEE = 0.0002
CASH = 10_000.0
WINDOW = 1400   # bars fed per step: > bear_gate_ma+100 so the in-strategy gate is live
GATE = 1200     # 50d in hours

_TMP = "/Users/pranavkrishna/.claude/jobs/a2a5a542/tmp"
MEGA = os.environ.get("MEGA_CACHE", f"{_TMP}/bars_mega.json")
REC8 = os.environ.get("REC8_CACHE", f"{_TMP}/bars_cache.json")

WINDOWS = [
    ("2021bull", MEGA, "2021-01-01", "2021-11-08"),
    ("2022bear", MEGA, "2022-01-01", "2023-01-01"),
    ("2023recov", MEGA, "2023-01-01", "2023-10-01"),
    ("2324bull", MEGA, "2023-10-01", "2024-03-14"),
    ("2024chop", MEGA, "2024-03-14", "2024-09-06"),
    ("late24", MEGA, "2024-09-06", "2025-01-20"),
    ("OOS", REC8, "2025-06-08", "2026-04-13"),
    ("tgt", REC8, "2026-04-13", "2026-07-13"),
    ("fullrec", REC8, "2025-06-08", None),
]


def load(cache):
    d = json.load(open(cache))
    uni, times = d["universe"], d["times"]
    bars = {s: [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                for r in d["bars"][s]] for s in uni}
    return uni, times, bars


def idx_of(times, iso):
    for i, t in enumerate(times):
        if t >= iso:
            return i
    return len(times)


def run(uni, times, bars, lo, hi, bear_gate_ma=0):
    pe = PortfolioEmulator(initial_cash=CASH, taker_fee=FEE)
    strat = Meanrev()
    cfg = {"band": "low", "top_k": 2, "regime_ma": 200, "rsi_buy": 35,
           "rsi_exit": 55, "sizing": "vol", "bear_gate_ma": bear_gate_ma,
           "allocations": []}
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
                cash = (float(sz["buy_cash"]) if isinstance(sz, dict) and "buy_cash" in sz
                        else pe.get_portfolio_value(prices) / 2.0)
                pe.execute_signal(s, 1, prices[s], timestamp=ct, cash_per_trade=cash)
        pe.save_portfolio_snapshot(prices, timestamp=ct)
    final = pe.get_portfolio_value({s: bars[s][hi - 1]["c"] for s in uni})
    return (final / CASH - 1) * 100


def main():
    _cache = {}
    print(f"{'window':10s} {'B&H':>8s} {'base':>8s} {'gated':>8s} {'delta':>7s}  verdict")
    print("-" * 60)
    wins_base = wins_gate = 0
    for name, cache, s0, e0 in WINDOWS:
        if cache not in _cache:
            _cache[cache] = load(cache)
        uni, times, bars = _cache[cache]
        lo = idx_of(times, s0 + "T00:00:00Z")
        hi = idx_of(times, e0 + "T00:00:00Z") if e0 else len(times)
        bh = (sum((CASH / len(uni)) / bars[s][lo]["c"] * (1 - FEE) * bars[s][hi - 1]["c"]
                  for s in uni) / CASH - 1) * 100
        base = run(uni, times, bars, lo, hi, bear_gate_ma=0)
        gt = run(uni, times, bars, lo, hi, bear_gate_ma=GATE)
        wb = (base >= bh) if bh > 0 else (base > 0)
        wg = (gt >= bh) if bh > 0 else (gt > 0)
        wins_base += wb
        wins_gate += wg
        print(f"{name:10s} {bh:+8.1f} {base:+8.2f} {gt:+8.2f} {gt - base:+7.2f}  "
              f"base={'W' if wb else '-'} gate={'W' if wg else '-'}", flush=True)
    print(f"\nfaithful wins: base {wins_base}/9, gated {wins_gate}/9")


if __name__ == "__main__":
    main()
