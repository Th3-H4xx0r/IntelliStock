#!/usr/bin/env python3
"""Measure how long each name waited between being seen and being bought.

    python3 scripts/measure_entry_lag.py 333727
    python3 scripts/measure_entry_lag.py 333727 826225      # compare two runs

The objective's blocker #1 is entry timing. The obvious statistic — "how far through its move
did we buy?" — needs the move's END price and is therefore lookahead-contaminated and can never
become a signal. Lag needs only the run's own log.

Named `measure_entry_lag` rather than `entry_lag`: a script shares its own directory as
sys.path[0], so a same-named script shadows the backend module it imports.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from entry_lag import compare, measure  # noqa: E402


def _log_text(backtest_id, cache_dir):
    out = Path(cache_dir) / f"bt{backtest_id}.log"
    if not out.is_file():
        subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "pull_backtest_logs.py"),
             str(backtest_id), "--out", str(out)],
            check=True, capture_output=True,
        )
    return out.read_text(encoding="utf-8", errors="replace")


def _report(bid, r):
    s = r["stats"]
    print(f"\n=== bt {bid} ===")
    for e in r["entries"]:
        print(f"  {e['symbol']:<6} considered {e['considered']} -> bought {e['bought']}"
              f"   {e['lag_days']:>3} d")
    if r["parse_errors"]:
        print(f"  !! {len(r['parse_errors'])} PARSE ERROR(S) — excluded, not clamped:")
        for e in r["parse_errors"]:
            print(f"     {e['symbol']:<6} {e.get('reason','')}")
    if s.get("n"):
        print(f"  n={s['n']}  median={s['median_days']} d  mean={s['mean_days']} d  "
              f"max={s['max_days']} d")
        print(f"  slow tail (>= {s['slow_threshold_days']} d): {s['slow_count']} "
              f"{s['slow_symbols']}")
    else:
        print("  no measurable entries")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("control_id")
    p.add_argument("treatment_id", nargs="?")
    p.add_argument("--cache-dir", default=".")
    a = p.parse_args(argv)

    ctl = measure(_log_text(a.control_id, a.cache_dir))
    _report(a.control_id, ctl)
    if not a.treatment_id:
        return 0

    trt = measure(_log_text(a.treatment_id, a.cache_dir))
    _report(a.treatment_id, trt)
    c = compare(ctl, trt)
    print(f"\n--- comparison ---")
    print(f"  control   median={c['control'].get('median_days')} d  "
          f"mean={c['control'].get('mean_days')} d  max={c['control'].get('max_days')} d")
    print(f"  treatment median={c['treatment'].get('median_days')} d  "
          f"mean={c['treatment'].get('mean_days')} d  max={c['treatment'].get('max_days')} d")
    print(f"  shared names: {c['shared_symbols'] or '(none)'}  overlap={c['overlap']:.0%}")
    if c["caveat"]:
        print(f"  CAVEAT: {c['caveat']}")
    return 0 if c["comparable"] else 2


if __name__ == "__main__":
    sys.exit(main())
