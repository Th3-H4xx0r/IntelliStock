#!/usr/bin/env python3
"""Pre-registered engine test for the outlier sleeve (spec §9). POST-only against
the LAB instance; never touches doc 200.

    python3 scripts/outlier_engine_test.py [--arm default|confirm50] [--windows spec|regime]

`--windows spec` (default) runs the seven pre-registered windows and applies the
frozen pass/fail rules. `--windows regime` runs the 25-window regime battery
(bear / bull / chop / handoff / calendar years / multi-year) for the comparison
table only. Appends to docs/superpowers/research/2026-09-02-outlier-sleeve-engine-test.md.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
from _api import call  # noqa: E402

INSTANCE = "strategy-eb-lab"
STOCKS = ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"]
SPEC_WINDOWS = [("cyc", "2021-11-01", "2026-08-27"), ("ny1", "2022-01-01", "2023-12-31"),
                ("ny3", "2024-01-01", "2026-08-27"), ("rb1", "2022-01-01", "2022-06-30"),
                ("rb3", "2025-02-15", "2025-04-15"), ("nb4", "2026-01-15", "2026-04-30"),
                ("nc3", "2022-03-01", "2022-08-31")]
REGIME_WINDOWS = [
    ("bear", "rb1", "2022-01-01", "2022-06-30"), ("bear", "rb2", "2026-02-01", "2026-04-01"),
    ("bear", "rb3", "2025-02-15", "2025-04-15"),
    ("bull", "ru1", "2023-01-01", "2023-07-31"), ("bull", "ru2", "2026-04-01", "2026-06-01"),
    ("bull", "ru3", "2024-01-01", "2024-06-30"), ("bull", "p21bull", "2021-01-01", "2021-10-31"),
    ("bull", "nu1", "2023-10-25", "2024-03-28"), ("bull", "nu2", "2024-08-06", "2024-12-31"),
    ("chop", "rc1", "2025-11-10", "2026-02-24"), ("chop", "rc2", "2022-07-01", "2022-12-31"),
    ("chop", "rc3", "2024-07-01", "2024-10-31"), ("chop", "nc1", "2023-02-01", "2023-06-15"),
    ("chop", "nc2", "2024-03-15", "2024-08-30"),
    ("handoff", "h1", "2021-11-01", "2021-12-31"), ("handoff", "h2", "2023-08-01", "2023-10-31"),
    ("handoff", "h3", "2025-05-01", "2025-10-31"), ("handoff", "h4", "2024-11-01", "2025-02-14"),
    ("handoff", "h5", "2026-06-01", "2026-08-27"),
    ("year", "y22", "2022-01-01", "2022-12-31"), ("year", "y23", "2023-01-01", "2023-12-31"),
    ("year", "y24", "2024-01-01", "2024-12-31"), ("year", "y25", "2025-01-01", "2025-12-31"),
    ("multi", "ny2", "2023-01-01", "2024-12-31"), ("multi", "cyc", "2021-11-01", "2026-08-27")]
# bil25 engine baselines (return %, maxDD %) already on file
BASE = {"cyc": (197.78, -21.1), "ny1": (36.00, -21.1), "ny3": (139.49, -13.5),
        "rb1": (2.06, -12.3), "rb3": (2.59, -6.9), "nb4": (1.94, -12.7), "nc3": (-11.20, -18.6),
        "rb2": (0.29, -11.3), "ru1": (25.97, -10.2), "ru2": (11.19, -4.8), "ru3": (18.23, -8.9),
        "p21bull": (18.82, -9.1), "nu1": (22.71, -6.3), "nu2": (9.85, -8.2), "rc1": (19.94, -8.5),
        "rc2": (3.38, -17.3), "rc3": (2.55, -14.4), "nc1": (13.20, -9.1), "nc2": (1.82, -12.4),
        "h1": (-3.21, -9.7), "h2": (-0.03, -5.6), "h3": (39.53, -7.1), "h4": (13.18, -8.2),
        "h5": (5.74, -9.2), "y22": (2.61, -21.1), "y23": (30.41, -11.5), "y24": (21.62, -13.5),
        "y25": (55.45, -8.0), "ny2": (54.31, -14.1)}
OUT = os.path.join(_ROOT, "docs", "superpowers", "research", "2026-09-02-outlier-sleeve-engine-test.md")


def maxdd(x):
    pk, m = x[0], 0.0
    for v in x:
        pk = max(pk, v)
        m = min(m, v / pk - 1)
    return m


def path_and_trades(bid):
    import psycopg
    dsn = (f"host=server7 port=5432 user=intellistock dbname=IntelliStock "
           f"password={os.environ['POSTGRES_PASSWORD']} options=-c\\ default_transaction_read_only=on")
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute('select doc from "BacktestSteps" where backtest_id=%s and kind=%s and doc is not null order by seq',
                    (str(bid), "pv"))
        pv = [r[0] for r in cur.fetchall()]
        cur.execute('select doc from "BacktestSteps" where backtest_id=%s and kind=%s and doc is not null order by seq',
                    (str(bid), "trade"))
        trades = [r[0] for r in cur.fetchall()]
    byday = {}
    for d in pv:
        if d and d.get("value") is not None and (d.get("prices") or {}).get("SPY"):
            byday[str(d["timestamp"])[:10]] = (float(d["value"]), float(d["prices"]["SPY"]))
    days = sorted(byday)
    nav = [byday[t][0] for t in days]
    spy = [byday[t][1] for t in days]
    f = lambda t: dt.date.fromisoformat(t)  # noqa: E731
    spytr = [v * (1.0125 ** ((f(days[i]) - f(days[0])).days / 365.25)) for i, v in enumerate(spy)]
    return days, nav, spytr, trades


def attribution(trades, excluded=frozenset(STOCKS)):
    """Realised cash P&L per non-ETF ticker from trade steps (sells minus buys).
    Open positions at the end are not marked here — a conservative under-count."""
    pnl = collections.defaultdict(float)
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        sym = str(t.get("ticker") or t.get("symbol") or "").upper()
        if not sym or sym in excluded:
            continue
        side = str(t.get("action") or t.get("side") or "").lower()
        cash = float(t.get("total") or 0.0) or float(t.get("shares") or 0) * float(t.get("price") or 0)
        pnl[sym] += cash if side.startswith("s") else -cash
    return dict(sorted(pnl.items(), key=lambda kv: -kv[1]))


def set_confirm_frac(value):
    _, inst = call("GET", f"/instances/{INSTANCE}")
    _, doc = call("GET", f"/strategies/{inst['strategy_id']}")
    for l in doc["strategies"]:
        if l["strategy"] == "outlier_sleeve":
            l["config"]["confirm_frac"] = value
    call("PUT", f"/strategies/{inst['strategy_id']}", {"name": doc["name"], "strategies": doc["strategies"]})


def run_windows(windows):
    ids = []
    for tag, s, e in windows:
        for _ in range(3):
            try:
                _, r = call("POST", "/backtests", {"instance_id": INSTANCE, "stocks": STOCKS, "start_date": s,
                                                   "end_date": e, "granularity": "86400", "initial_cash": 6000,
                                                   "equity_cost_tiers": "etf-liquid"})
                ids.append((r["id"], tag))
                break
            except BaseException:
                time.sleep(10)
    print("posted", ids, flush=True)
    done = {}
    for _ in range(400):
        time.sleep(30)
        try:
            _, b = call("GET", "/backtests")
        except BaseException:
            continue
        bts = {x.get("id"): x for x in (b.get("backtests", b) if isinstance(b, dict) else b)}
        for i, tag in ids:
            if tag not in done and bts.get(i, {}).get("status") in ("finished", "error", "stopped"):
                done[tag] = i
                print("done", tag, i, bts[i].get("status"), bts[i].get("pnl_percent"), flush=True)
        if len(done) == len(ids):
            break
    return done


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="default", choices=["default", "confirm50"])
    ap.add_argument("--windows", default="spec", choices=["spec", "regime"])
    args = ap.parse_args(argv)
    if args.arm == "confirm50":
        set_confirm_frac(0.50)
    try:
        windows = SPEC_WINDOWS if args.windows == "spec" else [(t, s, e) for _, t, s, e in REGIME_WINDOWS]
        regime_of = {t: r for r, t, _, _ in REGIME_WINDOWS}
        done = run_windows(windows)
        rows, checks = [], {}
        for tag, bid in done.items():
            days, nav, spytr, trades = path_and_trades(bid)
            if len(days) < 5:
                rows.append((tag, bid, "?", "?", float("nan"), float("nan"), float("nan"), float("nan"), BASE.get(tag, (float("nan"), float("nan")))))
                continue
            ret = (nav[-1] / nav[0] - 1) * 100
            dd = maxdd(nav) * 100
            s = (spytr[-1] / spytr[0] - 1) * 100
            rows.append((tag, bid, days[0], days[-1], ret, s, dd, maxdd(spytr) * 100, BASE.get(tag, (float("nan"), float("nan")))))
            if tag == "cyc":
                att = attribution(trades)
                gain = nav[-1] - nav[0]
                big = [k for k, v in att.items() if gain > 0 and v / gain >= 0.05]
                checks["c1_return"] = bool(ret >= BASE["cyc"][0] + 15)
                checks["c2_drawdown"] = bool(dd >= BASE["cyc"][1] - 3)
                checks["c4_population"] = bool(len(big) >= 3)
                checks["attribution_top"] = {k: round(v, 0) for k, v in list(att.items())[:12]}
        bears = [r for r in rows if r[0] in ("rb1", "rb3", "nb4", "rb2")]
        checks["c3_bears_nonnegative"] = bool(all(r[4] >= 0 for r in bears if r[8][0] >= 0))
        if args.windows == "spec":
            verdict = "PASS" if all(checks.get(k) for k in ("c1_return", "c2_drawdown", "c3_bears_nonnegative")) else "FAIL"
        else:
            wins = sum(1 for r in rows if r[4] == r[4] and r[4] > r[5])
            verdict = f"{wins}/{len(rows)} windows beat SPY-TR"
        title = f"# Outlier sleeve — engine test ({args.arm}, {args.windows} windows)"
        lines = [title, "", f"Instance {INSTANCE} · granularity 86400 · etf-liquid tiers · run {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%MZ}", "",
                 "| regime | window | bt | span | EB+sleeve | SPY-TR | Δ vs SPY | bil25 base | Δ vs base | DD | SPY DD | base DD |",
                 "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        order = ["bear", "bull", "chop", "handoff", "year", "multi"]
        for tag, bid, d0, d1, ret, s, dd, sdd, base in sorted(rows, key=lambda r: (order.index(regime_of.get(r[0], "multi")), r[0])):
            lines.append(f"| {regime_of.get(tag, '')} | {tag} | {bid} | {d0}..{d1} | {ret:+.2f}% | {s:+.2f}% | {ret - s:+.2f} | {base[0]:+.2f}% | {ret - base[0]:+.2f} | {dd:.1f}% | {sdd:.1f}% | {base[1]:.1f}% |")
        lines += ["", f"## Verdict: **{verdict}**", "", "```", json.dumps(checks, indent=1), "```"]
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "a") as fh:
            fh.write("\n".join(lines) + "\n\n")
        print("\n".join(lines), flush=True)
        return 0 if verdict == "PASS" or args.windows == "regime" else 1
    finally:
        if args.arm == "confirm50":
            set_confirm_frac(0.25)
            print("restored confirm_frac=0.25 on the lab doc", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
