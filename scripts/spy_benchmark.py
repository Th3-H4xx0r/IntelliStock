#!/usr/bin/env python3
"""Benchmark a backtest against SPY using the run's own SPY FILLS.

Why this exists: `spy_series` reads the monitor price stream and returned FOUR
samples for bt 523085 and bt 102463 (25 for the bear window). A benchmark built
from four points is not a benchmark, and every SPY comparison published in this
project before 2026-08-14 rested on it. SPY is traded in the core lane, so its
fill lines carry dated prices — 16-17 per run.

It also refuses to difference two series that do not span the same dates. On
2026-08-14 the SPY fills for bt 102463 stopped on 02-05 while the strategy ran to
02-26; differencing those is the same error as reading a stopped run's P&L.

Usage:
    python3 scripts/spy_benchmark.py <backtest_id> [--return PCT] [--log PATH]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

FILL = re.compile(
    r"FILL (?:BUY|SELL) (\S+) qty=[\d\.]+ cumulative=[\d\.]+ price=([\d\.]+)"
    r".*quote=(\d{4}-\d{2}-\d{2})")

# `benchmark_quote_logging_enabled` (broker.py:13709) prints the mark every tick,
# so the series no longer depends on the core lane happening to trade. Prefer it.
QUOTE = re.compile(
    r"BENCHMARK QUOTE: (\S+) (\d{4}-\d{2}-\d{2}) \d{2}:\d{2} ([\d\.]+)")


def spy_points(lines, symbol="SPY"):
    """Dated prices for `symbol`, de-duplicated and ordered.

    Returns `(points, source)`. Tick-logged quotes win outright when present:
    a fill series is a sample of the days the core lane traded, which is how
    every pre-2026-08-14 SPY comparison in this project ended up resting on
    four points.
    """
    quotes, fills = {}, {}
    for line in lines:
        q = QUOTE.search(line)
        if q and q.group(1) == symbol:
            quotes[q.group(2)] = float(q.group(3))
            continue
        m = FILL.search(line)
        if m and m.group(1) == symbol:
            fills[m.group(3)] = float(m.group(2))
    if quotes:
        return sorted(quotes.items()), "tick quotes"
    return sorted(fills.items()), "fills"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("backtest_id")
    ap.add_argument("--return", dest="ret", type=float, default=None,
                    help="strategy return %% for the same window, to difference")
    ap.add_argument("--log", default=None, help="use an existing log file")
    ap.add_argument("--floor", type=float, default=10.0,
                    help="noise floor in pp (measured ~10pp on 2026-08-14)")
    ap.add_argument("--symbol", default="SPY",
                    help="benchmark symbol (QQQ is logged too when tick "
                         "quote logging is on)")
    args = ap.parse_args(argv)

    if args.log:
        text = Path(args.log).read_text(errors="replace")
    else:
        out = Path(f"/tmp/bt{args.backtest_id}_spy.log")
        subprocess.run([sys.executable, "scripts/pull_backtest_logs.py",
                        args.backtest_id, "--out", str(out)],
                       check=False, capture_output=True, timeout=900)
        text = out.read_text(errors="replace")

    pts, source = spy_points(text.splitlines(), args.symbol)
    if len(pts) < 3:
        print(f"REFUSING: only {len(pts)} {args.symbol} {source} points "
              f"— not a benchmark.")
        return 2

    first, last = pts[0], pts[-1]
    spy = 100.0 * (last[1] - first[1]) / first[1]
    print(f"{args.symbol} points   : {len(pts)}  (from {source})")
    print(f"span         : {first[0]} -> {last[0]}")
    print(f"SPY return   : {spy:+.2f}%  (${first[1]:.2f} -> ${last[1]:.2f})")

    if args.ret is not None:
        delta = args.ret - spy
        verdict = "beat" if delta > args.floor else (
            "LOSES" if delta < -args.floor else "NOISE")
        print(f"strategy     : {args.ret:+.2f}%")
        print(f"vs SPY       : {delta:+.2f}pp  -> {verdict} (floor {args.floor:.1f}pp)")
        print("\nCHECK THE SPAN: if the SPY span above is shorter than the "
              "strategy's window, this difference is not meaningful.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
