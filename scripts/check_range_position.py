#!/usr/bin/env python3
"""OFFLINE check: where in its 20-session range was the regime proxy on the two
bars that decided the SQQQ bear leg?

gap-oos.md §6 lists this as the open question that could kill the fresh-low rule:
542754's 03-05 park is only INFERRED to be off the low. Runs are the scarce
resource, so read the bars we already have cached instead of spending one.

Reads AlpacaBarsCache (the same table backend/price_utils.py writes) read-only.
"""
from __future__ import annotations
import base64, gzip, json, os, sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "backend"))
from reset_backtest_event_state import conn as _conn  # noqa: E402
from db import store as _store  # noqa: E402

DB = "IntelliStock"


def daily_closes(c, symbol):
    """Every cached bar for `symbol`, collapsed to one close per session."""
    out = {}
    cur = _store.run(_store.filter("AlpacaBarsCache", {"symbol": symbol}))
    n_docs = 0
    for doc in cur:
        n_docs += 1
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
            t = str(b.get("t") or "")
            c_px = b.get("c")
            if not t or c_px is None:
                continue
            out[t[:10]] = float(c_px)   # later bar of a day wins = daily close
    return n_docs, OrderedDict(sorted(out.items()))


def range_pos(closes, on_date, window=20):
    """Exactly what graph_nexus_analysis stamps: bars_since_20d_low / pct_off."""
    days = [d for d in closes if d <= on_date]
    if len(days) < window:
        return None
    w = days[-window:]
    vals = [closes[d] for d in w]
    lo = min(vals)
    since = len(vals) - 1 - max(i for i, v in enumerate(vals) if v == lo)
    return {"date": on_date, "close": vals[-1], "low20": lo,
            "low_on": w[max(i for i, v in enumerate(vals) if v == lo)],
            "bars_since_20d_low": since,
            "pct_off_20d_low": round((vals[-1] - lo) / lo * 100.0, 2)}


# --- what the paired rule actually does, bar by bar, on 383778's four bear bars ---
#
# The fresh-low gate blocks the OPEN. `regime_rally_onset_enabled` (already
# written, currently OFF) is what covers the bars just AFTER the low. Neither is
# any use if a bar slips between them, so evaluate both predicates on real closes.

def rally_onset(closes, on_date, ma_bars, bounce_min=2.5, max_since_low=2):
    """Byte-for-byte the predicate in graph_nexus_analysis._rally_onset."""
    days = [d for d in closes if d <= on_date]
    if len(days) < 21:
        return None
    series = [closes[d] for d in days]
    current = series[-1]
    ma = sum(series[-ma_bars:]) / min(len(series), ma_bars)
    w = series[-20:]
    lo20 = min(w)
    bounce = (current - lo20) / lo20 * 100.0
    since = len(w) - 1 - max(i for i, v in enumerate(w) if v == lo20)
    return {"ma": ma, "reclaim": current > ma, "bounce": bounce,
            "since": since,
            "fires": bool(current > ma and bounce >= bounce_min and since <= max_since_low)}


def audit_383778(closes, sym):
    print(f"\n--- {sym}: 383778's four bear bars under fresh-low(N) + rally_onset ---")
    print("    bar         since_low  off_low   ma10 reclaim  bounce   rally_onset  "
          "blocked by")
    for d in ("2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02"):
        rp = range_pos(closes, d)
        ro10 = rally_onset(closes, d, 10)
        if rp is None or ro10 is None:
            print(f"    {d}  (insufficient history)")
            continue
        blockers = []
        if rp["bars_since_20d_low"] < 1:
            blockers.append("fresh_low N=1")
        elif rp["bars_since_20d_low"] < 2:
            blockers.append("fresh_low N=2")
        if ro10["fires"]:
            blockers.append("rally_onset(ma10)")
        print(f"    {d}   {rp['bars_since_20d_low']:6d}  {rp['pct_off_20d_low']:+7.2f}%   "
              f"{str(ro10['reclaim']):>7}  {ro10['bounce']:+6.2f}%   "
              f"{str(ro10['fires']):>6}       {' + '.join(blockers) or 'NOTHING — leg opens'}")


# --- the detector is POINT-IN-TIME: on bar D it has only closes through D-1 ---
#
# graph_nexus_analysis._daily_closes_from_intraday takes `date_str` + pit kwargs,
# so at 15:00 on 2026-03-30 the last COMPLETE session close is 2026-03-27. The
# replay table in the _rally_onset docstring (:7240) is therefore shifted one
# session against a naive "closes through D" reading — and that shift is exactly
# what decides whether rally_onset covers 04-02. Reproduce the docstring table
# before trusting either reading.

def pit_table(closes, sym, dates, lag=1, ma_bars=10):
    print(f"\n--- {sym}: detector view with PIT lag={lag} session(s) "
          f"(reproduces the _rally_onset docstring table when lag=1) ---")
    print("    decision bar   last close seen   ret20    >ma10   %off 20d low   "
          "bars since low")
    days = list(closes)
    for d in dates:
        idx = [i for i, x in enumerate(days) if x <= d]
        if not idx:
            continue
        end = idx[-1] - lag
        if end < 20:
            continue
        seen = days[: end + 1]
        series = [closes[x] for x in seen]
        current = series[-1]
        ret20 = (current - series[-21]) / series[-21] * 100.0
        ma = sum(series[-ma_bars:]) / min(len(series), ma_bars)
        w = series[-20:]
        lo = min(w)
        since = len(w) - 1 - max(i for i, v in enumerate(w) if v == lo)
        off = (current - lo) / lo * 100.0
        print(f"    {d}       {seen[-1]}        {ret20:+6.2f}   "
              f"{str(current > ma):>5}      {off:+6.2f}          {since:2d}")


def ma_margin(closes, sym, dates, lag=1, ma_bars=10):
    """How close is the >ma10 reclaim to its boundary? The docstring replay and
    this cached-bars replay DISAGREE on 04-01 (True vs False), so the size of the
    margin decides whether that bar can be relied on at all."""
    print(f"\n--- {sym}: ma{ma_bars} reclaim margin (PIT lag={lag}) ---")
    days = list(closes)
    for d in dates:
        idx = [i for i, x in enumerate(days) if x <= d]
        if not idx:
            continue
        end = idx[-1] - lag
        if end < ma_bars:
            continue
        series = [closes[x] for x in days[: end + 1]]
        current = series[-1]
        ma = sum(series[-ma_bars:]) / ma_bars
        print(f"    {d}  close={current:8.2f}  ma{ma_bars}={ma:8.2f}  "
              f"margin={current - ma:+7.2f} ({(current/ma - 1) * 100:+5.2f}%)  "
              f"reclaim={current > ma}")


def main():
    c = _conn()
    # 542754 ran SPY proxy on 03-05; 383778 ran QQQ proxy on 03-30. Check both
    # symbols on both dates so the answer does not hinge on proxy selection.
    for sym in ("SPY", "QQQ"):
        n_docs, closes = daily_closes(c, sym)
        print(f"\n=== {sym}: {n_docs} cached chunk doc(s), {len(closes)} session closes "
              f"({next(iter(closes), '?')} .. {next(reversed(closes), '?')})")
        for label, d in (("542754 first park (+$889 leg)", "2026-03-05"),
                         ("542754 alt first-park bar",    "2026-03-04"),
                         ("383778 bad park (-$257 leg)",  "2026-03-30"),
                         ("383778 bar 2",                 "2026-03-31"),
                         ("383778 bar 3",                 "2026-04-01"),
                         ("383778 bar 4",                 "2026-04-02")):
            rp = range_pos(closes, d)
            if rp is None:
                print(f"  {d}  {label:32} — not enough cached history")
                continue
            print(f"  {d}  {label:32} close={rp['close']:9.2f} "
                  f"low20={rp['low20']:9.2f} (set {rp['low_on']})  "
                  f"since_20d_low={rp['bars_since_20d_low']:3d}  "
                  f"off_low={rp['pct_off_20d_low']:+7.2f}%")
        audit_383778(closes, sym)
        pit_table(closes, sym,
                  ["2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02", "2026-04-03"])
        pit_table(closes, sym, ["2026-03-04", "2026-03-05", "2026-03-06"])
        ma_margin(closes, sym,
                  ["2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02"])
    c.close()


if __name__ == "__main__":
    main()
