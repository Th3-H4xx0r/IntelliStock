"""Forward-test scorecard (FROZEN.md). Zero tuning: every rule constant lives in the frozen modules.

    python3 score_forward.py                    # refresh ./cache from yfinance/FRED, score from 2026-11-09
    python3 score_forward.py --no-fetch         # reuse ./cache
    python3 score_forward.py --start 2027-01-04 --end 2027-06-30
    python3 score_forward.py --data snapshot --no-fetch     # the frozen snapshot (no forward sessions)

Each strategy is simulated continuously from its fixed inception (strategies.INCEPTION) to the latest close;
signals use close t, trades fill at close t+1, 5 bps per unit traded (23 bps stress row), margin at T-bill +
0.5%/yr. The forward window is that NAV rebased at the base close (the last session before --start); SPY total
return is rebased at the same close. History before the start is warm-up only and is never scored.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategies as S  # noqa: E402
from fetch_data import build_frame, fetch  # noqa: E402
from harness import _mdd, _window_returns, extend_calendar, judge, nyse_sessions, WIN_EPS  # noqa: E402

DEFAULT_START = "2026-11-09"
COSTS = (5.0, 23.0)


def first_session_on_or_after(d):
    d = pd.Timestamp(d)
    return nyse_sessions(d, d + pd.Timedelta(days=10))[0]


def window_stats(nav, bench, months):
    e1, r1 = _window_returns(nav, months)
    e2, r2 = _window_returns(bench, months)
    if not len(r1):
        return None, 0
    return float(np.mean((r1 - r2) > WIN_EPS)), int(len(r1))


def trailing(nav, bench, months):
    """Simple-return excess (pp) over the trailing `months` calendar months, None if the span is shorter."""
    t = nav.index[-1]
    t0 = t - pd.DateOffset(months=months)
    if t0 < nav.index[0]:
        return None
    k = nav.index.searchsorted(t0, side="right") - 1
    return ((nav.iloc[-1] / nav.iloc[k]) - (bench.iloc[-1] / bench.iloc[k])) * 100


def score(nav_full, spy, start):
    base_i = int(nav_full.index.searchsorted(start)) - 1
    if base_i < 0:
        raise ValueError("forward start precedes the strategy's inception")
    nav = nav_full.iloc[base_i:]
    nav = nav / nav.iloc[0]
    b = spy.reindex(nav.index)
    b = b / b.iloc[0]
    out = {"base": nav.index[0], "last": nav.index[-1], "sessions": len(nav) - 1}
    if len(nav) < 2:
        return out
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    out.update(cum=(nav.iloc[-1] - 1) * 100, cum_b=(b.iloc[-1] - 1) * 100, years=yrs, months=yrs * 12)
    out["cum_ex"] = out["cum"] - out["cum_b"]
    out["ann"] = ((nav.iloc[-1]) ** (1 / yrs) - 1) * 100
    out["ann_b"] = ((b.iloc[-1]) ** (1 / yrs) - 1) * 100
    out["ann_ex"] = out["ann"] - out["ann_b"]
    out["mdd"], out["mdd_b"] = _mdd(nav) * 100, _mdd(b) * 100
    for m in (3, 6, 12, 36):
        out[f"w{m}"] = window_stats(nav, b, m)
    out["t12"] = trailing(nav, b, 12)
    out["nav"], out["bench"] = nav, b
    return out


def fmt_w(w):
    share, n = w
    return "-" if share is None else f"{share*100:.0f}% ({n})"


def weights_line(w):
    keys = sorted((k for k in w if k not in ("tbill", "cash")), key=lambda k: -abs(w[k]))
    parts = [f"{S.TICKER.get(k, k)} {w[k]:+.3f}" for k in keys]
    cash = w.get("tbill", w.get("cash"))
    if cash is not None:
        parts.append(f"cash {cash:+.3f}")
    return ", ".join(parts)


def sso_line(w):
    e = w.get("market", w.get("core", 0.0))
    if e <= 1.0 + 1e-9:
        return None
    return (f"optional SSO form of the {e:.3f}x SPY leg: SPY {2 - e:.3f} + SSO {e - 1:.3f} "
            f"(same S&P exposure, less margin; the score is computed for SPY on margin)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START, help="first forward session on/after this date")
    ap.add_argument("--end", default=None, help="last close to score (default: latest available)")
    ap.add_argument("--data", default="cache", help="data dir (relative to this folder): cache | snapshot")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--save", default=None, help="write the forward daily NAVs to this CSV")
    a = ap.parse_args()
    ddir = a.data if os.path.isabs(a.data) else os.path.join(HERE, a.data)
    if not a.no_fetch:
        fetch(ddir)
    P = build_frame(ddir, end=a.end)
    last = P.index[-1]
    start = first_session_on_or_after(a.start)
    nxt = extend_calendar(P.index)[len(P)]
    runs = S.run_all(P, costs=COSTS)
    spy = P["market"]

    print(f"FORWARD TEST 2026-11 -- frozen hunt finalists (FROZEN.md). Data through {last.date()} "
          f"({os.path.basename(ddir)}); next session {nxt.date()}.")
    print(f"Forward start {start.date()} (base close = last session before it). Benchmark SPY total return. "
          f"Primary {S.PRIMARY}.\n")
    rows, navs = [], {}
    for name in S.RUNNERS:
        for c in COSTS:
            sc = score(runs[name][c]["nav"], spy, start)
            rows.append((name, c, sc))
            if "nav" in sc:
                navs[f"{name}@{c:.0f}"] = sc["nav"]
                navs["SPY_TR"] = sc["bench"]
    if all(sc["sessions"] < 1 for _, _, sc in rows):
        print(f"No forward session has closed yet (data end {last.date()} < start {start.date()}).\n")
    else:
        print("| strategy | bps | sessions | cum % / SPY % | cum excess pp | ann. excess pp/yr | maxDD % / SPY % "
              "| won 3m | won 6m | won 12m | won 36m | trailing-12m excess pp |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for name, c, sc in rows:
            if sc["sessions"] < 1:
                continue
            t12 = "-" if sc["t12"] is None else f"{sc['t12']:+.2f}"
            print(f"| {name} | {c:.0f} | {sc['sessions']} | {sc['cum']:+.2f} / {sc['cum_b']:+.2f} | {sc['cum_ex']:+.2f} | "
                  f"{sc['ann_ex']:+.2f} | {sc['mdd']:.1f} / {sc['mdd_b']:.1f} | {fmt_w(sc['w3'])} | {fmt_w(sc['w6'])} | "
                  f"{fmt_w(sc['w12'])} | {fmt_w(sc['w36'])} | {t12} |")
        months = max(sc.get("months", 0) for _, _, sc in rows)
        print(f"\nElapsed: {months:.1f} months. Annualised figures under 12 months are noise; FROZEN.md decisions "
              f"start at month 12. Windows: share of daily-stepped rolling windows won (count).")
        p5 = next(sc for n, c, sc in rows if n == S.PRIMARY and c == 5.0)
        if p5.get("months", 0) >= 12:
            print("\nFROZEN.md decision checks for the primary (5 bps):")
            print(f"  ann. excess > 0 ............ {'yes' if p5['ann_ex'] > 0 else 'NO'} ({p5['ann_ex']:+.2f})")
            print(f"  maxDD no deeper than SPY ... {'yes' if p5['mdd'] >= p5['mdd_b'] - 1e-9 else 'NO'} "
                  f"({p5['mdd']:.1f} vs {p5['mdd_b']:.1f})")
            print(f"  trailing-12m excess >= -5 .. {'yes' if p5['t12'] >= -5 else 'NO -> KILL'} ({p5['t12']:+.2f})")
            if p5.get("months", 0) >= 36:
                w12 = p5["w12"][0]
                print(f"  12m windows won >= 80% ..... {'yes' if w12 >= 0.8 else 'NO'} ({w12*100:.0f}%)")

    print(f"\nTarget weights for the next session ({nxt.date()}), as fractions of NAV, fill at that close:")
    for name in S.RUNNERS:
        nx = runs[name][5.0]["next"]
        if nx["action"] == "TRADE":
            what = f" ({' + '.join(nx['which'])} decision)" if "which" in nx else ""
            print(f"- {name}: TRADE{what} at the {nxt.date()} close -> {weights_line(nx['target'])}")
            sl = sso_line(nx["target"])
        else:
            print(f"- {name}: HOLD (no decision at the {last.date()} close); drifted book -> {weights_line(nx['current'])}")
            sl = sso_line(nx["current"])
        if sl:
            print(f"    {sl}")
    if a.save and navs:
        pd.DataFrame(navs).to_csv(a.save, float_format="%.8f")
        print(f"\nforward NAVs written to {a.save}")


if __name__ == "__main__":
    main()
