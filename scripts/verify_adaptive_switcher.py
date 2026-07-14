"""FAITHFUL verify of the ADAPTIVE regime switcher before implementation.
Bull (basket >= 200d ramped MA AND >= 50d ramped MA, computed from the window
the production strategy would see: fetch starts 90d(2160 bars) before the
backtest window) -> hold EW basket (buy non-held once, hold; sell all on flip).
Bear -> delegate to the REAL shipped Meanrev with bear_gate_ma=1200 through the
REAL PortfolioEmulator (Binance.US 0.02%). Fast harness says mean +27, bears
2022/OOS/tgt/fullrec positive, chop -23. This decides go/no-go."""
import sys, json, datetime as dt
import numpy as np

sys.path.insert(0, "/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/backend")
from portfolio_emulator import PortfolioEmulator
from strategies.crypto.meanrev import Meanrev

FEE = 0.0002
CASH = 10_000.0
MR_WINDOW = 1400        # slice fed to Meanrev (in-strategy 1200h gate live)
PREFETCH = 2160         # 90 calendar days of hourly bars before window start
SW_MA, CF_MA = 4800, 1200

TMP = "/Users/pranavkrishna/.claude/jobs/a2a5a542/tmp"
REC8 = f"{TMP}/bars_cache.json"
MEGA = f"{TMP}/bars_mega.json"

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
    closes = {s: np.array([r[4] for r in d["bars"][s]], dtype=float) for s in uni}
    return uni, times, bars, closes


def idx_of(times, iso):
    for i, t in enumerate(times):
        if t >= iso:
            return i
    return len(times)


def bull_series(uni, closes, lo, hi):
    """bull[i] for i in [lo,hi): basket (anchored at avail_start = lo-PREFETCH,
    clamped) >= ramped SMA(SW_MA) AND >= ramped SMA(CF_MA)."""
    a0 = max(0, lo - PREFETCH)
    n = hi - a0
    basket = np.mean([closes[s][a0:hi] / closes[s][a0] for s in uni], axis=0)
    cs = np.cumsum(np.insert(basket, 0, 0.0))
    idx = np.arange(1, n + 1)
    def ramp(ma):
        w = np.minimum(idx, ma)
        return (cs[idx] - cs[idx - w]) / w
    slow, fast = ramp(SW_MA), ramp(CF_MA)
    bull = (basket >= slow) & (basket >= fast)
    return bull[lo - a0:]  # aligned to i-lo


def run_switcher(uni, times, bars, lo, hi, bull):
    pe = PortfolioEmulator(initial_cash=CASH, taker_fee=FEE)
    strat = Meanrev()
    cfg = {"band": "low", "top_k": 2, "regime_ma": 200, "rsi_buy": 35,
           "rsi_exit": 55, "sizing": "vol", "bear_gate_ma": 1200,
           "allocations": []}
    prev_bull = None
    for i in range(lo, hi):
        prices = {s: bars[s][i]["c"] for s in uni}
        ct = dt.datetime.strptime(times[i][:19], "%Y-%m-%dT%H:%M:%S")
        b = bool(bull[i - lo])
        held = set((pe.get_positions() or {}).keys())
        if b:
            # bull: buy every non-held coin at pv/N once; hold the rest
            pv = pe.get_portfolio_value(prices)
            per = pv / len(uni)
            for s in uni:
                if s not in held and per > 1.0:
                    pe.execute_signal(s, 1, prices[s], timestamp=ct, cash_per_trade=per)
        else:
            if prev_bull:  # flip bull->bear: liquidate the basket
                for s in list(held):
                    pe.execute_signal(s, -1, prices[s], timestamp=ct, sell_fraction=1.0)
            else:
                w0 = max(0, i - MR_WINDOW + 1)
                data = {s: bars[s][w0:i + 1] for s in uni}
                res = strat.run_once(symbols=list(uni), prices=prices, current_time=ct,
                                     config=cfg, conditions={}, data=data,
                                     portfolio_emulator=pe, mode="LIVE")
                if isinstance(res, dict):
                    sizes = res.get("_nexus_position_sizes")
                    sizes = sizes if isinstance(sizes, dict) else {}
                    sig = {k: v for k, v in res.items()
                           if isinstance(k, str) and not k.startswith("_")}
                    for s, v in sig.items():
                        if v == -1:
                            pe.execute_signal(s, -1, prices[s], timestamp=ct, sell_fraction=1.0)
                    for s, v in sig.items():
                        if v == 1:
                            sz = sizes.get(s)
                            cash = (float(sz["buy_cash"]) if isinstance(sz, dict) and "buy_cash" in sz
                                    else pe.get_portfolio_value(prices) / 2.0)
                            pe.execute_signal(s, 1, prices[s], timestamp=ct, cash_per_trade=cash)
        prev_bull = b
        pe.save_portfolio_snapshot(prices, timestamp=ct)
    final = pe.get_portfolio_value({s: bars[s][hi - 1]["c"] for s in uni})
    return (final / CASH - 1) * 100


_cache = {}
print(f"{'window':10s} {'B&H':>8s} {'switch':>8s}  bull%   verdict")
print("-" * 52)
wins = 0
for name, cache, s0, e0 in WINDOWS:
    if cache not in _cache:
        _cache[cache] = load(cache)
    uni, times, bars, closes = _cache[cache]
    lo = idx_of(times, s0 + "T00:00:00Z")
    hi = idx_of(times, e0 + "T00:00:00Z") if e0 else len(times)
    bull = bull_series(uni, closes, lo, hi)
    bh = (sum((CASH / len(uni)) / bars[s][lo]["c"] * (1 - FEE) * bars[s][hi - 1]["c"]
              for s in uni) / CASH - 1) * 100
    r = run_switcher(uni, times, bars, lo, hi, bull)
    w = (r >= bh) if bh > 0 else (r > 0)
    wins += w
    print(f"{name:10s} {bh:+8.1f} {r:+8.2f}  {bull.mean() * 100:4.0f}%   {'W' if w else '-'}",
          flush=True)
print(f"\nfaithful switcher wins: {wins}/9")
