#!/usr/bin/env python3
"""One-screen verdict for a finished backtest: return, drawdown, churn, and who
actually made the money.

The handoff table ("window | bt | result | maxDD | churn | top contributors")
has been rebuilt by hand from raw JSON every time. This prints it, and prints
the two things that are easy to skip and expensive to skip: the SLEEVE legs
broken out from the stock book (SQQQ was 124% of one window's profit and -90%
of another's), and the SPY core's post-initial churn.

    python3 scripts/summarize_backtest.py 584886
    python3 scripts/summarize_backtest.py 571147 337615 321638   # compare
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv, _login, _http  # noqa: E402

SLEEVE_SYMS = {"SPY", "SQQQ"}


def _api_and_token():
    _load_dotenv(_REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL") or os.environ.get("API_URL")
           or f"http://localhost:{os.environ.get('API_PORT', '8000')}").rstrip("/")
    token = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
        api,
        os.environ.get("INTELLISTOCK_USERNAME") or os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
        os.environ.get("INTELLISTOCK_PASSWORD") or os.environ.get("DEFAULT_ADMIN_PASSWORD", ""),
    )
    return api, token


def summarize(api, token, bid):
    st, s = _http("GET", f"{api}/backtests/{bid}/summary",
                  headers={"Authorization": f"Bearer {token}"})
    if st != 200 or not isinstance(s, dict):
        print(f"#{bid}: summary -> {st} {str(s)[:200]}")
        return
    status = s.get("status")
    start = str(s.get("start_date") or "")[:10]
    end = str(s.get("end_date") or "")[:10]
    cash = s.get("initial_cash")
    print(f"\n=== bt {bid}  {start}..{end}  ${cash}  {s.get('instance_id')}  [{status}] ===")
    if status != "finished":
        print("  (not finished — numbers below may be partial)")

    pnl_pct = s.get("pnl_percent")
    rm = s.get("risk_metrics") or {}
    dd = rm.get("max_drawdown_pct")
    ch = s.get("sleeve_churn") or {}
    gross = ch.get("gross_notional") or 0.0
    post = ch.get("post_initial_gross_notional") or 0.0
    nav0 = float(cash or 0) or 1.0
    print(f"  return    {pnl_pct:+.2f}%   (${s.get('pnl'):+,.2f} -> ${s.get('portfolio_end_value'):,.2f})"
          if pnl_pct is not None else "  return    n/a")
    if dd is not None:
        print(f"  max DD    {dd * 100:.1f}%   (peak ${rm.get('max_drawdown_peak_value', 0):,.0f} "
              f"-> trough ${rm.get('max_drawdown_trough_value', 0):,.0f})")
    print(f"  trades    {s.get('total_trades')} ({s.get('total_buys')}B/{s.get('total_sells')}S), "
          f"{s.get('round_trips')} round trips, win rate {s.get('win_rate_percent') or 0:.0f}%, "
          f"round-trip P&L ${s.get('total_round_trip_pnl') or 0:+,.2f}")
    print(f"  core lane {ch.get('fill_count')} SPY fills, gross ${gross:,.0f} "
          f"({gross / nav0:.2f}x NAV), post-initial ${post:,.0f} ({post / nav0:.2f}x NAV)")
    el = s.get("time_elapsed_seconds")
    if el:
        print(f"  wall      {el / 60:.0f} min")

    per = s.get("pnl_per_stock") or {}
    if not per:
        return
    stock = {k: v for k, v in per.items() if k not in SLEEVE_SYMS}
    sleeve = {k: v for k, v in per.items() if k in SLEEVE_SYMS}
    total = sum(per.values()) or 1.0
    print("  contributors (stock book):")
    for sym, v in sorted(stock.items(), key=lambda kv: -abs(kv[1]))[:8]:
        print(f"      {sym:<6} {v:+9.2f}   {v / total * 100:+6.1f}% of total P&L")
    if sleeve:
        print("  SLEEVE legs:")
        for sym, v in sorted(sleeve.items(), key=lambda kv: -abs(kv[1])):
            print(f"      {sym:<6} {v:+9.2f}   {v / total * 100:+6.1f}% of total P&L")
    winners = [v for v in stock.values() if v > 0]
    print(f"  book      {len(stock)} names, {len(winners)} up; "
          f"best {max(stock.values(), default=0):+.0f}, worst {min(stock.values(), default=0):+.0f}")

    # Is the objective's mechanism present? One name has to matter.
    if stock:
        top = max(stock.values())
        print(f"  CONCENTRATION: top name = {top / nav0 * 100:+.1f}% of starting NAV "
              f"({'one winner mattered' if top / nav0 >= 0.05 else 'NO single name moved the needle'})")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("backtest_ids", nargs="+", type=int)
    a = p.parse_args(argv)
    api, token = _api_and_token()
    for bid in a.backtest_ids:
        summarize(api, token, bid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
