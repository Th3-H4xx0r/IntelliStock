#!/usr/bin/env python3
"""Benchmark return for a window, from the bars already cached. No run needed.

The objective is "beat SPY in every regime", but not one investigation states
what SPY did in the windows being validated. A +13.35% OOS run and a +10.44%
bear run are not comparable achievements if SPY did +12.79% in one and -6% in
the other -- and the OOS window is in fact the one where SPY runs hard, which is
what makes releasing the core there expensive.

    python3 scripts/benchmark_window.py 2026-06-01 2026-07-01
    python3 scripts/benchmark_window.py --validation-set
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from collections import OrderedDict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from reset_backtest_event_state import conn as _conn  # noqa: E402
from rethinkdb import RethinkDB  # noqa: E402

r = RethinkDB()
DB = "IntelliStock"

# The windows the objective's validation clause names, plus the ones the
# handoff reports results for.
VALIDATION_SET = [
    ("reference bull/chop", "2026-01-01", "2026-03-01"),
    ("bear",                "2026-03-02", "2026-03-30"),
    ("OOS bull",            "2026-03-30", "2026-04-27"),
    ("non-semi-led",        "2026-06-01", "2026-07-01"),
    ("forward untested",    "2026-04-27", "2026-06-27"),
    ("run-up into ref",     "2025-11-01", "2026-01-01"),
]


def daily_closes(c, symbol):
    out = {}
    for doc in r.db(DB).table("AlpacaBarsCache").filter(
            r.row["symbol"].eq(symbol)).run(c):
        raw = doc.get("bars")
        if not raw:
            continue
        try:
            if doc.get("compressed"):
                raw = gzip.decompress(base64.b64decode(raw)).decode("utf-8")
            bars = json.loads(raw)
        except Exception:
            continue
        for b in bars or []:
            t, px = str(b.get("t") or ""), b.get("c")
            if t and px is not None:
                out[t[:10]] = float(px)
    return OrderedDict(sorted(out.items()))


def window_return(closes, start, end):
    days = [d for d in closes if start <= d <= end]
    if len(days) < 2:
        return None
    a, b = closes[days[0]], closes[days[-1]]
    lo, hi = min(closes[d] for d in days), max(closes[d] for d in days)
    # max drawdown of the benchmark itself, so "beat SPY" can be read on a
    # risk-adjusted basis and not just on the endpoint.
    peak, mdd = closes[days[0]], 0.0
    for d in days:
        peak = max(peak, closes[d])
        mdd = min(mdd, closes[d] / peak - 1.0)
    return {"first": days[0], "last": days[-1], "n": len(days),
            "ret": (b / a - 1.0) * 100.0, "lo": lo, "hi": hi,
            "mdd": mdd * 100.0}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("start", nargs="?")
    p.add_argument("end", nargs="?")
    p.add_argument("--symbols", default="SPY,QQQ")
    p.add_argument("--validation-set", action="store_true")
    a = p.parse_args(argv)

    c = _conn()
    series = {s: daily_closes(c, s) for s in a.symbols.split(",")}
    windows = VALIDATION_SET if a.validation_set else [("", a.start, a.end)]
    if not a.validation_set and not (a.start and a.end):
        print("give START END, or --validation-set")
        return 1

    for label, start, end in windows:
        print(f"\n{label or 'window'}  {start} .. {end}")
        for sym, closes in series.items():
            w = window_return(closes, start, end)
            if not w:
                print(f"    {sym:<5} no cached bars in range")
                continue
            print(f"    {sym:<5} {w['ret']:+7.2f}%   maxDD {w['mdd']:6.2f}%   "
                  f"({w['n']:3d} sessions {w['first']} .. {w['last']})")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
