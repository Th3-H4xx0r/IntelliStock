#!/usr/bin/env python3
"""Run the validation suite doc-193 owes before anyone talks about real money.

Everything is staged: the levers are set on doc-193 and the deployed build is
hash-verified. This runs the windows in order, one at a time, and stops the
moment a run pauses on credits rather than queueing five more that will pause
too.

    python3 scripts/run_validation_suite.py            # all windows
    python3 scripts/run_validation_suite.py --only ref # just the reference
    python3 scripts/run_validation_suite.py --dry-run  # print the plan

WHY THESE WINDOWS. The objective requires at least 3, including at least 1 OOS
and at least 1 where leadership is not semiconductors, "otherwise you are
fitting to one name". `ref` is the tuning window and is NOT evidence on its own;
it is here to confirm the pending levers do what the log analysis predicts:

  * max_positions_exclude_sleeve_legs  — bt 820236 sized SNDK at $873 (14.6% of
    NAV), funded it, waved it through the turnover brake, then refused it with
    `MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)` on a line reading
    open_pos=5. Freeing the core's slot is the last gate in its way.
  * rank_band_momentum_exempt_min_score — the band refused 2,833 buys including
    VICR (60d=+119.6%, blocked x6, never bought) while admitting WDC
    (60d=+37.5%) and LRCX (60d=+31.9%), which carried the run.
  * turnover_budget_conviction_bypass_max_pct — the bypass ran turnover to 105%
    of NAV against a 50% budget and churned $11,186 of SPY around a $2,398 core.

WHAT TO READ IN EACH LOG, not just the return:
  * `FILL BUY SNDK` — one entry near 01-12 at ~$388, ~14% of NAV. Three dribbles
    starting 01-20 is the failure mode we are trying to remove.
  * `Rank band: N momentum name(s) exempt` — the exemption firing at all.
  * `TURNOVER BYPASS CEILING` — the brake holding above 80%.
  * SPY fill count and post-initial gross — the churn leak.

DO NOT run the 1-year window at 3600s. bt 591989 measured ~29 hours of wall
clock for it; the regime-sliced windows below cover bull, bear and chop for a
fraction of the credit.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (key, start, end, what it is for)
WINDOWS = [
    ("ref",      "2026-01-01", "2026-03-01", "tuning window — mechanism check, NOT evidence"),
    ("bear",     "2026-03-02", "2026-03-30", "bear — the SQQQ leg (bt 342380 made +18.71% here)"),
    ("bull_oos", "2026-03-30", "2026-04-27", "OOS bull"),
    ("bear_oos", "2026-04-27", "2026-05-25", "OOS bear"),
    ("oos_2025a","2025-10-06", "2025-11-03", "OOS, earliest data — leadership unmeasured"),
    ("oos_2025b","2025-12-01", "2025-12-29", "OOS bear"),
]

REQUIRED_LEVERS = {
    "max_positions_exclude_sleeve_legs": True,
    "rank_band_momentum_exempt_min_score": 0.8,
    "turnover_budget_conviction_bypass_max_pct": 0.8,
    "total_spend_cap_concentrate": True,
    "core_max_pct": 0.4,
    "min_position_nav_pct": 0.06,
}


def _run(cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          cwd=str(REPO), timeout=timeout)


def preflight() -> bool:
    """Refuse to spend credits on a build that is not the one we think it is."""
    out = _run("python3 scripts/check_deployed_code.py")
    print(out.stdout.strip())
    if out.returncode != 0:
        print("!! deployed code does not match the working tree — redeploy first")
        return False
    return True


def launch(start: str, end: str) -> int | None:
    out = _run(
        f"python3 scripts/run_validation_backtest.py {start} {end} "
        f"--cash 6000 --granularity 3600 --instance v2-let-run-core")
    try:
        return int(json.loads(out.stdout.strip())["id"])
    except Exception:
        print(f"!! could not launch: {out.stdout.strip()[:200]} {out.stderr.strip()[:200]}")
        return None


def _status(bt_id: int) -> dict:
    """Status straight off the API, reusing pull_backtest_logs' auth."""
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "pbl", REPO / "scripts" / "pull_backtest_logs.py")
    pbl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pbl)
    pbl._load_dotenv(REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL") or "").rstrip("/")
    token = (os.environ.get("INTELLISTOCK_API_TOKEN") or "").strip()
    if not token:
        token = pbl._login(
            api,
            os.environ.get("INTELLISTOCK_USERNAME")
            or os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
            os.environ.get("INTELLISTOCK_PASSWORD")
            or os.environ.get("DEFAULT_ADMIN_PASSWORD", ""),
        )
    code, body = pbl._fetch(api, token, f"/backtests/{bt_id}/status", allow_404=True)
    return body if code == 200 and isinstance(body, dict) else {}


def poll(bt_id: int, every: int = 120):
    """Block until terminal. Returns the final status string."""
    while True:
        try:
            st = _status(bt_id)
        except Exception:
            st = {}
        if not st:
            time.sleep(every)
            continue
        state = str(st.get("status") or "")
        print(f"    {bt_id} {state} {st.get('progress')}%")
        if state in {"finished", "complete", "error", "stopped", "paused_credits"}:
            return state
        time.sleep(every)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run one window by key")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    plan = [w for w in WINDOWS if not a.only or w[0] == a.only]
    if not plan:
        print(f"no window named {a.only!r}; known: {[w[0] for w in WINDOWS]}")
        return 2

    print("PLAN")
    for key, start, end, why in plan:
        print(f"  {key:10} {start} .. {end}   {why}")
    print("")
    print("REQUIRED LEVERS on doc-193 strategies[0].config:")
    for k, v in REQUIRED_LEVERS.items():
        print(f"  {k} = {v!r}")
    if a.dry_run:
        return 0

    if not preflight():
        return 1

    results = []
    for key, start, end, _why in plan:
        print(f"\n=== {key}  {start}..{end}")
        # Reset the mutable active-event state before every run. Without it the
        # run inherits whatever the previous one left in GraphNexusActiveEvents,
        # `current_events` differs from day one, and the maintenance cache takes
        # a legitimate miss on every call — 42 batches and $1.47 on bt 718249.
        # The cache table itself is preserved, so the second and later runs of a
        # window replay from the same cold baseline and HIT. It is also what
        # makes two arms comparable at all.
        rst = _run("python3 scripts/reset_backtest_event_state.py "
                   f"--instance v2-let-run-core --apply")
        print("    " + " | ".join(l.strip() for l in rst.stdout.strip().splitlines()
                                  if "row(s)" in l))
        bt_id = launch(start, end)
        if bt_id is None:
            return 1
        state = poll(bt_id)
        results.append((key, bt_id, state))
        if state == "paused_credits":
            print("\n!! PAUSED ON CREDITS — stopping the suite rather than "
                  "queueing runs that will pause too. Top up OpenRouter and "
                  "re-run; completed windows above do not need repeating.")
            break

    print("\nRESULTS")
    for key, bt_id, state in results:
        print(f"  {key:10} bt {bt_id}  {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
