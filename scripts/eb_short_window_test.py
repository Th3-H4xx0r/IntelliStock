#!/usr/bin/env python3
"""Pre-registered short-window test of Strategy EB candidates (docs/superpowers/research/
2026-09-03-short-window-preregistration.md). Sequential engine runs on the LAB doc/instance
only; doc 200 is never touched.

    python3 scripts/eb_short_window_test.py            # all four candidates
    python3 scripts/eb_short_window_test.py K1 K3      # a subset
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from _api import call  # noqa: E402

INSTANCE = "strategy-eb-lab"
STOCKS = ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"]
WINDOWS = [("cyc", "2021-11-01", "2026-08-27"), ("rb1", "2022-01-01", "2022-06-30"),
           ("rb2", "2026-02-01", "2026-04-01"), ("rb3", "2025-02-15", "2025-04-15")]
BIL25_ON_BOOK = {"GLD": 0.5, "GDX": 0.25, "XLE": 0.25}
CANDIDATES = {
    "K1": {"trend_on_book": {"QQQ": 0.5, "GLD": 0.25, "GDX": 0.125, "XLE": 0.125}},
    "K2": {"trend_on_book": {"QQQ": 0.25, "GLD": 0.375, "GDX": 0.1875, "XLE": 0.1875}},
    "K3": {"target_vol": 0.25},
    "K4": {"target_vol": 0.25, "trend_on_book": {"QQQ": 0.25, "GLD": 0.375, "GDX": 0.1875, "XLE": 0.1875}},
}
VTS_CANDIDATES = {
    "V1": {"vts_enabled": True, "vts_threshold": 1.00, "vts_median_bars": 250},
    "V2": {"vts_enabled": True, "vts_threshold": 1.05, "vts_median_bars": 250},
}
BIL25_3M = 0.616
OUT = os.path.join(_ROOT, "docs", "superpowers", "research", "2026-09-03-short-window-preregistration.md")
OUT_VTS = os.path.join(_ROOT, "docs", "superpowers", "research", "2026-09-04-vts-reentry-preregistration.md")


def maxdd(x):
    pk, m = x[0], 0.0
    for v in x:
        pk = max(pk, v)
        m = min(m, v / pk - 1)
    return m


def path(bid):
    import psycopg
    dsn = (f"host=server7 port=5432 user=intellistock dbname=IntelliStock "
           f"password={os.environ['POSTGRES_PASSWORD']} options=-c\\ default_transaction_read_only=on")
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute('select doc from "BacktestSteps" where backtest_id=%s and kind=%s and doc is not null order by seq',
                    (str(bid), "pv"))
        rows = [r[0] for r in cur.fetchall() if isinstance(r[0], dict)]
    byday = {}
    for d in rows:
        if d.get("value") is not None and (d.get("prices") or {}).get("SPY"):
            byday[str(d["timestamp"])[:10]] = (float(d["value"]), float(d["prices"]["SPY"]))
    days = sorted(byday)
    nav = [byday[t][0] for t in days]
    spy = [byday[t][1] for t in days]
    f = lambda t: dt.date.fromisoformat(t)  # noqa: E731
    spytr = [v * (1.0125 ** ((f(days[i]) - f(days[0])).days / 365.25)) for i, v in enumerate(spy)]
    return days, nav, spytr


def beat(nav, spytr, W):
    n = len(nav)
    wins = sum(1 for i in range(W, n) if nav[i] / nav[i - W] > spytr[i] / spytr[i - W])
    return wins / max(1, n - W)


def lab_doc():
    _, inst = call("GET", f"/instances/{INSTANCE}")
    _, doc = call("GET", f"/strategies/{inst['strategy_id']}")
    return inst["strategy_id"], doc


def put_doc(doc_id, doc):
    call("PUT", f"/strategies/{doc_id}", {"name": doc["name"], "strategies": doc["strategies"]})


def run_one(tag, s, e):
    bid = None
    for _ in range(3):
        try:
            _, r = call("POST", "/backtests", {"instance_id": INSTANCE, "stocks": STOCKS, "start_date": s,
                                               "end_date": e, "granularity": "86400", "initial_cash": 6000,
                                               "equity_cost_tiers": "etf-liquid"})
            bid = r["id"]
            break
        except BaseException:
            time.sleep(10)
    if bid is None:
        return None, None
    print("posted", tag, bid, flush=True)
    for _ in range(400):
        time.sleep(20)
        try:
            _, b = call("GET", "/backtests")
        except BaseException:
            continue
        bts = {x.get("id"): x for x in (b.get("backtests", b) if isinstance(b, dict) else b)}
        st = bts.get(bid, {}).get("status")
        if st in ("finished", "error", "stopped"):
            print("done", tag, bid, st, bts[bid].get("pnl_percent"), flush=True)
            return bid, st
    return bid, "timeout"


def main(argv=None):
    global CANDIDATES, OUT
    args = list(argv or sys.argv[1:])
    if "--set" in args:
        i = args.index("--set")
        if args[i + 1] == "vts":
            CANDIDATES, OUT = VTS_CANDIDATES, OUT_VTS
        del args[i:i + 2]
    wanted = [a for a in args if a in CANDIDATES] or list(CANDIDATES)
    doc_id, doc = lab_doc()
    original = copy.deepcopy(doc)
    eb = next(l for l in doc["strategies"] if l["strategy"] == "strategy_eb")
    assert eb["config"]["trend_off_book"] == {"GLD": 0.375, "GDX": 0.1875, "XLE": 0.1875}, "lab EB lane is not bil25"
    lines = [f"\n### Run {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%MZ} — candidates {', '.join(wanted)}\n",
             "| cand | cyc bt | beat 3m | beat 6m | beat 12m | rb1 | rb2 | rb3 | cycle | maxDD | T1 | T2 | T3 | T4 | T5 | T6 | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    try:
        for cand in wanted:
            d = copy.deepcopy(original)
            for l in d["strategies"]:
                if l["strategy"] == "strategy_eb":
                    l["config"]["trend_on_book"] = dict(BIL25_ON_BOOK)
                    l["config"]["target_vol"] = 0.20
                    l["config"]["reserve_for_other_lanes_pct"] = 0.0
                    l["config"].update(copy.deepcopy(CANDIDATES[cand]))
                if l["strategy"] == "outlier_sleeve":
                    l["config"]["outlier_sleeve_enabled"] = False
            put_doc(doc_id, d)
            print(cand, "configured", json.dumps(CANDIDATES[cand]), flush=True)
            res = {}
            for tag, s, e in WINDOWS:
                bid, st = run_one(tag, s, e)
                if bid is None or st != "finished":
                    res[tag] = (bid, None)
                    continue
                days, nav, spytr = path(bid)
                res[tag] = (bid, (nav[-1] / nav[0] - 1, maxdd(nav), beat(nav, spytr, 63), beat(nav, spytr, 126), beat(nav, spytr, 252)))
            cyc = res["cyc"][1]
            if cyc is None:
                lines.append(f"| {cand} | {res['cyc'][0]} | — | — | — | — | — | — | — | — | — | — | — | — | — | — | RUN FAILED |")
                continue
            ret, dd, b3, b6, b12 = cyc
            bears = [res[t][1][0] if res[t][1] else float("nan") for t in ("rb1", "rb2", "rb3")]
            T = {"T1": b3 >= 0.70 and b3 >= BIL25_3M + 0.10, "T2": b6 >= 0.80, "T3": b12 >= 0.92,
                 "T4": all(x == x and x >= 0 for x in bears), "T5": ret >= 1.90, "T6": dd >= -0.24}
            verdict = "PASS" if all(T.values()) else "FAIL"
            lines.append(f"| {cand} | {res['cyc'][0]} | {b3:.1%} | {b6:.1%} | {b12:.1%} | {bears[0]:+.2%} | {bears[1]:+.2%} | {bears[2]:+.2%} | {ret:+.1%} | {dd:.1%} | "
                         + " | ".join("✓" if T[k] else "✗" for k in ("T1", "T2", "T3", "T4", "T5", "T6")) + f" | **{verdict}** |")
            print(lines[-1], flush=True)
    finally:
        put_doc(doc_id, original)
        print("lab doc restored", flush=True)
    with open(OUT, "a") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
