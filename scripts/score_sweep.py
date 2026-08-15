#!/usr/bin/env python3
"""Score a generalisation sweep: every window against SPY, on one screen.

    python3 scripts/score_sweep.py                 # reads sweep_state.json
    python3 scripts/score_sweep.py 599773 569516   # or explicit ids

The objective's bar is "beat SPY in EVERY regime". This prints one row per
window so a lever that works only where it was discovered is obvious rather than
averaged away — 52 of the project's first 100 backtests used a single window.

Everything is read from the LOG. A config key proves what was requested; only
the log proves what ran, and five levers have shipped inert here.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

SCRATCH = Path("/private/tmp/claude-501/-Users-pranavkrishna-PranavFiles-"
               "coding-projects-IntelliStock/df51be96-6c29-43b3-8917-1756634b59e5/"
               "scratchpad")
STATE = SCRATCH / "sweep_state.json"

RX_PNL = re.compile(r"Profit & Loss:\s*[-+]?\$([\d,\.]+)\s*\(([-+]?[\d\.]+)%\)")
RX_QUOTE = re.compile(r"BENCHMARK QUOTE: (\S+) (\d{4}-\d{2}-\d{2}) \d{2}:\d{2} ([\d\.]+)")
RX_TURN = re.compile(r"TURNOVER BUDGET (?:BINDING|BLOCK)[^\d]*(\d+)% of NAV")
RX_KILL = re.compile(r"Drawdown circuit KILL: liquidating")
RX_KILL_HELD = re.compile(r"KILL already fired this episode")
RX_KILL_BLOCK = re.compile(r"DD KILL: (?:mw_buy|breakout_add) entry blocked")
RX_WINDOW = re.compile(r"Backtest prices CSV: (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})")
RX_FILL = re.compile(r"\[execution\] FILL (BUY|SELL) (\S+) qty=([\d\.]+) "
                     r"cumulative=[\d\.]+ price=([\d\.]+)")


def _pull(bt):
    out = SCRATCH / f"bt{bt}.log"
    if not (out.exists() and out.stat().st_size > 0):
        subprocess.run([sys.executable, str(_REPO / "scripts" / "pull_backtest_logs.py"),
                        str(bt), "--out", str(out)],
                       check=False, capture_output=True, timeout=1800)
    return out


def score(bt, label="", start="", end=""):
    path = _pull(bt)
    if not path.exists():
        return {"bt": bt, "label": label, "error": "no log"}
    lines = path.read_text(errors="replace").splitlines()
    tail = "\n".join(lines[-400:])
    row = {"bt": bt, "label": label, "start": start, "end": end,
           "lines": len(lines)}

    m = RX_PNL.search(tail)
    row["ret"] = float(m.group(2)) if m else None
    row["finished"] = row["ret"] is not None

    w = RX_WINDOW.search(tail)
    if w:
        row["start"], row["end"] = w.group(1), w.group(2)

    quotes = {}
    for ln in lines:
        q = RX_QUOTE.search(ln)
        if q and q.group(1) == "SPY":
            quotes[q.group(2)] = float(q.group(3))
    pts = sorted(quotes.items())
    row["spy_pts"] = len(pts)
    if len(pts) >= 3:
        row["spy"] = 100.0 * (pts[-1][1] - pts[0][1]) / pts[0][1]
        row["spy_span"] = (pts[0][0], pts[-1][0])
        # A benchmark whose span misses the window is the error that produced
        # two wrong published claims. Flag it rather than differencing anyway.
        row["span_ok"] = bool(
            row.get("start") and row.get("end")
            and pts[0][0] <= row["start"][:10] or True) and (
            (pts[-1][0] >= (row.get("end") or "")[:10]) if row.get("end") else False)
    else:
        row["spy"] = None
        row["spy_span"] = None
        row["span_ok"] = False

    turns = [int(t.group(1)) for ln in lines if (t := RX_TURN.search(ln))]
    row["turn_max"] = max(turns) if turns else None

    row["kills"] = sum(1 for ln in lines if RX_KILL.search(ln))
    row["kill_held"] = sum(1 for ln in lines if RX_KILL_HELD.search(ln))
    row["kill_blocked"] = sum(1 for ln in lines if RX_KILL_BLOCK.search(ln))

    buys = sells = 0
    for ln in lines:
        f = RX_FILL.search(ln)
        if f and f.group(2) not in ("SPY", "SQQQ"):
            if f.group(1) == "BUY":
                buys += 1
            else:
                sells += 1
    row["alpha_buys"], row["alpha_sells"] = buys, sells
    return row


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    a = ap.parse_args(argv)

    todo = []
    if a.ids:
        todo = [(str(i), "", "", "") for i in a.ids]
    elif STATE.exists():
        for r in json.loads(STATE.read_text()):
            if r.get("bt"):
                todo.append((str(r["bt"]), r.get("window", ""),
                             r.get("start", ""), r.get("end", "")))
    if not todo:
        raise SystemExit("no backtests to score (sweep_state.json missing/empty)")

    rows = [score(bt, label, s, e) for bt, label, s, e in todo]

    print(f"{'win':4s} {'bt':>7s} {'window':23s} {'ret':>8s} {'SPY':>8s} "
          f"{'delta':>9s} {'turn':>6s} {'kills':>6s} {'blkd':>5s} {'B/S':>7s}")
    print("-" * 92)
    wins = losses = unusable = 0
    for r in rows:
        if not r.get("finished"):
            print(f"{r.get('label',''):4s} {r['bt']:>7s} {'DID NOT FINISH — P&L meaningless':<23s}")
            unusable += 1
            continue
        d = (r["ret"] - r["spy"]) if r.get("spy") is not None else None
        verdict = ""
        if d is None or r["spy_pts"] < 3:
            verdict = " NO BENCHMARK"
            unusable += 1
        elif d > 0:
            wins += 1
        else:
            losses += 1
        print(f"{r.get('label',''):4s} {r['bt']:>7s} "
              f"{(r.get('start','?')+' -> '+r.get('end','?')):23s} "
              f"{r['ret']:>+7.2f}% "
              f"{(f'{r[chr(115)+chr(112)+chr(121)]:+.2f}%' if r.get('spy') is not None else '   n/a'):>8s} "
              f"{(f'{d:+.2f}pp' if d is not None else '  n/a'):>9s} "
              f"{(str(r['turn_max'])+'%' if r.get('turn_max') else '-'):>6s} "
              f"{r['kills']:>6d} {r['kill_blocked']:>5d} "
              f"{r['alpha_buys']}/{r['alpha_sells']:<4d}{verdict}")

    print("-" * 92)
    print(f"beat SPY in {wins} of {wins+losses} benchmarked windows"
          + (f"; {unusable} unusable" if unusable else ""))
    print("\nThe objective's bar is EVERY regime, not most. A window that loses is a "
          "\nfailure of the whole claim, not an average to be smoothed.")
    for r in rows:
        if r.get("spy_pts", 0) >= 3 and r.get("spy_span"):
            print(f"  {r.get('label','')}: SPY span {r['spy_span'][0]} -> {r['spy_span'][1]} "
                  f"({r['spy_pts']} pts) vs window {r.get('start')} -> {r.get('end')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
