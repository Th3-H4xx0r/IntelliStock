#!/usr/bin/env python3
"""The scorecard the objective actually asks for: every finished run vs its BENCHMARK.

"Beat SPY in every regime" cannot be read off `pnl_percent`. A +13.35% run in a window
where SPY did +13.10% is a tie; a +3.09% run where SPY did -1.71% is a 4.8pp win. This
joins each finished backtest to the SPY/QQQ return of its own window (from cached bars,
no extra runs) and marks each against BOTH bars in the objective:

    beat SPY?          alpha > 0
    1x pace?           +12% per 2 months, pro-rated by calendar length

    python3 scripts/scorecard.py                  # all finished v2-let-run-core runs
    python3 scripts/scorecard.py --min-id 300000  # just this session's
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv, _login, _http  # noqa: E402
from benchmark_window import daily_closes, window_return  # noqa: E402
from reset_backtest_event_state import conn as _conn  # noqa: E402

TARGET_2MO = 12.0  # objective: 1x = +12% per two months
NOISE_FLOOR_PP = 4.94


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--instance", default="v2-let-run-core")
    p.add_argument("--min-id", type=int, default=0)
    p.add_argument("--pages", type=int, default=4)
    a = p.parse_args(argv)

    _load_dotenv(_REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL") or "").rstrip("/")
    token = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
        api, os.environ.get("INTELLISTOCK_USERNAME") or os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
        os.environ.get("INTELLISTOCK_PASSWORD") or os.environ.get("DEFAULT_ADMIN_PASSWORD", ""))
    auth = {"Authorization": f"Bearer {token}"}

    runs = []
    for page in range(1, a.pages + 1):
        st, body = _http("GET", f"{api}/backtests?page={page}&per_page=50", headers=auth)
        items = (body or {}).get("backtests") or []
        if not items:
            break
        runs.extend(items)

    c = _conn()
    spy = daily_closes(c, "SPY")
    c.close()

    rows = []
    for r_ in runs:
        if r_.get("instance") != a.instance or r_.get("status") != "finished":
            continue
        if int(r_.get("id", 0)) < a.min_id:
            continue
        s, e = str(r_.get("start_date"))[:10], str(r_.get("end_date"))[:10]
        bench = window_return(spy, s, e)
        if not bench:
            continue
        ret = float(r_.get("pnl_percent") or 0.0)
        d0 = date.fromisoformat(s)
        d1 = date.fromisoformat(e)
        months = max((d1 - d0).days / 30.44, 0.2)
        target = TARGET_2MO * months / 2.0
        rows.append((r_["id"], s, e, months, ret, bench["ret"], ret - bench["ret"], target))

    rows.sort(key=lambda x: (x[1], x[0]))
    print(f"{'bt':>7}  {'window':<24} {'len':>5}  {'run':>8} {'SPY':>8} {'alpha':>8}  "
          f"{'1x bar':>7}  verdict")
    print("-" * 92)
    for bid, s, e, months, ret, bench, alpha, target in rows:
        beat = "beat SPY" if alpha > 0 else "LOST to SPY"
        pace = "1x" if ret >= target else "below 1x"
        noise = "" if abs(alpha) >= NOISE_FLOOR_PP else "  (alpha < noise floor)"
        print(f"{bid:>7}  {s}..{e}  {months:4.1f}m  {ret:+7.2f}% {bench:+7.2f}% {alpha:+7.2f}pp  "
              f"{target:+6.1f}%  {beat}, {pace}{noise}")

    if rows:
        n = len(rows)
        won = sum(1 for r_ in rows if r_[6] > 0)
        paced = sum(1 for r_ in rows if r_[4] >= r_[7])
        print("-" * 92)
        print(f"  {won}/{n} beat SPY   {paced}/{n} at or above 1x pace   "
              f"mean alpha {sum(r_[6] for r_ in rows) / n:+.2f}pp")
        print(f"  NOTE: repeat runs of the SAME window and config have shown 0/18 held-name "
              f"overlap; treat any alpha below {NOISE_FLOOR_PP}pp as unresolved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
