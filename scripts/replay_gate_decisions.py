#!/usr/bin/env python3
"""Replay a finished backtest log and ask what the staged levers WOULD have done.

Credits are exhausted, so the pending fixes cannot be validated by running them.
This gets as close as is honest without a run: it reconstructs the book from the
recorded fills, walks every gate refusal in the log, and reports which ones the
staged levers would have changed.

It proves GATE ARITHMETIC, not P&L. A freed slot is not a guaranteed fill and
certainly not a guaranteed profit -- the name still has to clear every gate
below, and the book it changes then diverges from the recorded one. Treat the
output as an upper bound on how many refusals each lever addresses.

    python3 scripts/replay_gate_decisions.py backtests/820236_*.log

Levers modelled:
  max_positions_exclude_sleeve_legs     — does dropping the sleeve legs from the
                                          held count free the slot?
  rank_band_momentum_exempt_min_score   — how many rank-band refusals carried a
                                          momentum score at or above the cutoff?
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

SLEEVE = {"SPY", "SQQQ"}

RE_FILL = re.compile(
    r"FILL (BUY|SELL) (\S+) qty=([\d.]+) cumulative=\S+ price=([\d.]+).*?quote=(\d{4}-\d{2}-\d{2})")
RE_MPG = re.compile(r"MAX_POSITIONS_GATE: blocked (\w+) \(held=(\d+), cap=(\d+)\)")
RE_RANKBAND = re.compile(r"Rank band \(entry<=#(\d+), exit>#\d+ of (\d+)\): blocked (\d+) buy\(s\) \[([^\]]*)\]")
RE_MOM = re.compile(r"top3=\[([^\]]*)\]")


def replay(lines):
    book: dict[str, float] = {}
    mpg_events = []
    momentum_seen: dict[str, float] = {}
    rankband_blocked = collections.Counter()
    rankband_total = 0

    for line in lines:
        m = RE_FILL.search(line)
        if m:
            side, sym, qty = m.group(1), m.group(2), float(m.group(3))
            book[sym] = book.get(sym, 0.0) + (qty if side == "BUY" else -qty)
            if book[sym] <= 1e-9:
                book.pop(sym, None)
            continue

        # remember the best momentum score we ever saw for a ticker
        mm = RE_MOM.search(line)
        if mm:
            for tick, score in re.findall(r"\('(\w+)',\s*([\d.]+)\)", mm.group(1)):
                val = float(score)
                if val > momentum_seen.get(tick, 0.0):
                    momentum_seen[tick] = val

        m = RE_MPG.search(line)
        if m:
            sym, held, cap = m.group(1), int(m.group(2)), int(m.group(3))
            legs_held = sum(1 for s in book if s in SLEEVE)
            mpg_events.append({
                "symbol": sym, "held": held, "cap": cap,
                "legs_in_book": legs_held,
                "freed": legs_held > 0 and (held - legs_held) < cap,
            })
            continue

        m = RE_RANKBAND.search(line)
        if m:
            rankband_total += int(m.group(3))
            for tick in [t.strip() for t in m.group(4).split(",") if t.strip() and "..." not in t]:
                rankband_blocked[tick] += 1

    return mpg_events, momentum_seen, rankband_blocked, rankband_total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--momentum-cutoff", type=float, default=0.80)
    a = ap.parse_args(argv)

    path = Path(a.log)
    if not path.is_file():
        print(f"no such log: {path}")
        return 2
    lines = path.read_text(errors="replace").splitlines()

    mpg, momentum, rb_blocked, rb_total = replay(lines)

    print("=" * 70)
    print(f"GATE REPLAY — {path.name}")
    print("=" * 70)
    print("Proves gate arithmetic only. A freed slot is not a fill, and the")
    print("book diverges from the recorded one the moment anything changes.")
    print("")

    print(f"max_positions_exclude_sleeve_legs")
    print(f"  MAX_POSITIONS_GATE refusals ......... {len(mpg)}")
    freed = [e for e in mpg if e["freed"]]
    print(f"  would have had a slot freed ......... {len(freed)}"
          f"  ({len(freed) / len(mpg) * 100:.0f}%)" if mpg else "")
    by_sym = collections.Counter(e["symbol"] for e in freed)
    if by_sym:
        print(f"  names ............................... "
              f"{', '.join(f'{s}x{n}' for s, n in by_sym.most_common(10))}")
    print("")

    print(f"rank_band_momentum_exempt_min_score = {a.momentum_cutoff}")
    print(f"  rank-band buy refusals (total) ...... {rb_total}")
    print(f"  distinct names sampled in the log ... {len(rb_blocked)}")
    exempt = {s: momentum.get(s, 0.0) for s in rb_blocked
              if momentum.get(s, 0.0) >= a.momentum_cutoff}
    print(f"  of those, above the cutoff .......... {len(exempt)}")
    if exempt:
        top = sorted(exempt.items(), key=lambda kv: -kv[1])[:10]
        print(f"  would now be admitted ............... "
              f"{', '.join(f'{s}({v:.2f})' for s, v in top)}")
    print("")
    print("NOTE: the log only prints the first 8 blocked tickers per bar, so the")
    print("rank-band names are a SAMPLE. The refusal count is the full total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
