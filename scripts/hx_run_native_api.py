#!/usr/bin/env python3
"""Sequential API controller for the Strategy HX battery.

    python3 scripts/hx_run_native_api.py --variant V1 --window rb3
    python3 scripts/hx_run_native_api.py --variant V1 --window all-bear
    python3 scripts/hx_run_native_api.py --variant V1 --window all

STRICTLY SEQUENTIAL: refuse if any job is on the engine, post ONE window, poll
until it is terminal, archive it, print a verdict. Parallel posts were vetoed
by the operator on 2026-09-03. There is no simulation and no strategy
execution here — every number comes back from the engine.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from hx_lab_setup import DOC_NAME, INSTANCE_ID, assert_writable  # noqa: E402
from strategy_hx import DEFAULTS, strategy_hx_universe  # noqa: E402

#: Copied verbatim from scripts/outlier_engine_test.py:33-46 so HX is judged
#: on the SAME windows every other candidate on this engine was judged on.
_REGIME_WINDOWS = [
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
WINDOWS = {tag: (regime, start, end)
           for regime, tag, start, end in _REGIME_WINDOWS}

#: The five preregistered bear books, in run order. Config changes only.
VARIANTS = {
    "V1": {"PSQ": 0.60, "BIL": 0.40},
    "V2": {"SQQQ": 0.25, "BIL": 0.75},
    "V3": {"SH": 0.60, "BIL": 0.40},
    "V4": {"GLD": 0.30, "BIL": 0.70},
    "V5": {"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40},
}

#: SPY total return per window, from the spec's gate table. Windows with no
#: recorded number print "SPY n/a" rather than a guess.
SPY_BENCHMARK = {"rb1": -19.40, "rb2": -5.84, "rb3": -11.40,
                 "rc1": 2.08, "rc2": 2.24, "rc3": 7.03, "cyc": 77.11}

#: The order the spec freezes: bear first (cheapest kill), then chop, bull,
#: cycle.
BEAR_ORDER = ("rb3", "rb2", "rb1")
FULL_ORDER = BEAR_ORDER + ("rc1", "rc2", "rc3", "ru1", "ru3", "cyc")

#: Same starting cash as every prior EB run on this engine
#: (.claude/worktrees/main-session/output/research/bear-slow-full-2026-09-08/
#: NATIVE-PREREGISTRATION.json: "initial_cash": 6000). Hard-coded so a run is
#: comparable to the bil25 card without depending on an untracked worktree.
INITIAL_CASH = 6000
DRAWDOWN_REJECT = 0.25
POLL_SECONDS = 20
# 6 hours at 20s. The `cyc` window is ~1,200 sessions; a two-hour ceiling gave
# up on a job that was still running, and a window nobody waited for is a
# window that was never measured.
POLL_LIMIT = 1080
OUT_ROOT = os.path.join(_ROOT, "output", "research", "hx-2026-09-10")
_BUSY = ("running", "pending", "queued", "paused")


def engine_busy(call) -> list:
    """Ids of jobs occupying the engine, paginated to the full total.

    A clipped listing reads as an idle engine, which is exactly how two
    containers end up on it at once.
    """
    _, page = call("GET", "/backtests?per_page=100")
    rows = list(page.get("backtests") or [])
    for number in range(2, int(page.get("total_pages") or 1) + 1):
        _, extra = call("GET", f"/backtests?per_page=100&page={number}")
        rows.extend(extra.get("backtests") or [])
    total = page.get("total")
    if isinstance(total, int) and len(rows) < total:
        raise SystemExit(
            f"queue listing truncated: {len(rows)} of {total}. Refusing to "
            "post: an unseen row may be a running job.")
    return [r.get("id") for r in rows if r.get("status") in _BUSY]


def variant_config(variant) -> dict:
    return {**DEFAULTS, "strategy_hx_enabled": True,
            "bear_book": dict(VARIANTS[variant])}


def post_body(variant, start, end) -> dict:
    cfg = variant_config(variant)
    return {"instance_id": INSTANCE_ID,
            "stocks": sorted(set(strategy_hx_universe(cfg)) | {"SPY"}),
            "start_date": start, "end_date": end,
            "granularity": "86400", "initial_cash": INITIAL_CASH,
            "equity_cost_tiers": "etf-liquid", "evidence_mode": "off"}


def apply_variant(call, variant) -> str:
    """PUT the variant's bear book onto the lab doc. Returns the doc id."""
    _, inst = call("GET", f"/instances/{INSTANCE_ID}")
    doc_id = assert_writable(inst["strategy_id"])
    _, doc = call("GET", f"/strategies/{doc_id}")
    if doc.get("name") != DOC_NAME:
        raise SystemExit(
            f"doc {doc_id} is named {doc.get('name')!r}, not {DOC_NAME!r}. "
            "Refusing to write a document this script did not create.")
    lanes = [dict(lane) for lane in (doc.get("strategies") or [])]
    hx = [lane for lane in lanes if lane.get("strategy") == "strategy_hx"]
    if len(hx) != 1:
        raise SystemExit(f"expected exactly one strategy_hx lane, saw {len(hx)}")
    hx[0]["config"] = variant_config(variant)
    call("PUT", f"/strategies/{doc_id}", {"name": doc["name"],
                                          "strategies": lanes})
    return str(doc_id)


def drawdown_of(summary):
    """The engine's own drawdown metric as a fraction, or None.

    Never computed here. A number outside [0, 1], a bool, or a missing key is
    None, and the caller treats None as "not yet measurable" rather than as
    "safe".
    """
    metrics = summary.get("risk_metrics") if isinstance(summary, dict) else None
    if not isinstance(metrics, dict):
        return None
    raw = metrics.get("max_drawdown_pct")
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value


def summary_or_none(call, bid):
    """The job's summary, or None while there is not one yet.

    The summary row does not exist until the engine CLAIMS the job, and until
    then the endpoint raises ValueError -> HTTP 400, which `_api.call` turns
    into SystemExit. Read without this guard the controller dies on its FIRST
    poll, so the 25% risk stop, the archive and the verdict never run at all.

    Only 400 and 404 read as "not measurable yet". A 500 or an auth failure
    means the drawdown this loop exists to watch is unreadable, and running
    blind past the stop is worse than stopping.
    """
    try:
        _, summary = call("GET", f"/backtests/{bid}/summary")
    except SystemExit as error:
        text = str(error).lower()
        if any(f"http {code}" in text for code in ("400", "404")):
            return None
        raise
    return summary


def should_stop(call, bid) -> bool:
    drawdown = drawdown_of(summary_or_none(call, bid))
    return drawdown is not None and drawdown >= DRAWDOWN_REJECT


def archive(call, bid, out_dir) -> dict:
    """Save summary (scalars only), full logs and graph-data. Returns summary."""
    os.makedirs(out_dir, exist_ok=True)
    _, summary = call("GET", f"/backtests/{bid}/summary")
    scalars = {k: v for k, v in (summary or {}).items()
               if k not in ("equity_curve", "portfolio_values", "nav_series")}
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(scalars, fh, indent=1, default=str)
    for endpoint, name in (("logs", "logs.json"),
                           ("graph-data", "graph-data.json")):
        _, body = call("GET", f"/backtests/{bid}/{endpoint}")
        with open(os.path.join(out_dir, name), "w") as fh:
            json.dump(body, fh, default=str)
    return scalars


def verdict(variant, tag, summary) -> str:
    try:
        ret = float(summary.get("pnl_percent"))
    except (TypeError, ValueError):
        ret = float("nan")
    drawdown = drawdown_of(summary)
    spy = SPY_BENCHMARK.get(tag)
    spy_text = f"SPY {spy:+.2f}%" if spy is not None else "SPY n/a"
    delta = f" | delta {ret - spy:+.2f}" if spy is not None else ""
    dd_text = f"{drawdown * 100:.1f}%" if drawdown is not None else "n/a"
    return (f"{variant} {tag} [{WINDOWS[tag][0]}] {ret:+.2f}% vs {spy_text}"
            f"{delta} | maxDD {dd_text}")


def run_window(call, variant, tag, *, sleep=time.sleep) -> dict:
    busy = engine_busy(call)
    if busy:
        raise SystemExit(f"engine occupied: {busy}. One job at a time.")
    regime, start, end = WINDOWS[tag]
    apply_variant(call, variant)
    body = post_body(variant, start, end)
    # Timestamped: re-running a window must never overwrite pre-registered
    # evidence. The request body, the logs and the summary of the earlier run
    # are the record of what was actually posted, and a silent overwrite turns
    # a falsifiable result into whichever run finished last.
    out_dir = os.path.join(OUT_ROOT, f"{variant}-{tag}-{time.strftime('%H%M%S')}")
    with open(os.path.join(_ensure(out_dir), "request.json"), "w") as fh:
        json.dump(body, fh, indent=1)
    # An uncertain POST is never retried: a duplicated job would put two
    # containers on the engine, which is exactly what the veto forbids.
    _, accepted = call("POST", "/backtests", body, retries=1)
    bid = accepted["id"]
    print(f"POSTED {variant} {tag} {bid}", flush=True)

    stopped = False
    for _ in range(POLL_LIMIT):
        _, state = call("GET", f"/backtests/{bid}/status")
        status = state.get("status")
        if status in ("finished", "completed", "error", "stopped", "failed"):
            summary = archive(call, bid, out_dir)
            print(f"ARCHIVED {variant} {tag} {bid} {status}", flush=True)
            print(verdict(variant, tag, summary), flush=True)
            return {"id": bid, "status": status, "summary": summary,
                    "stopped": stopped, "out_dir": out_dir}
        if not stopped and should_stop(call, bid):
            print(f"RISK STOP {variant} {tag} {bid}: drawdown >= "
                  f"{DRAWDOWN_REJECT:.0%}", flush=True)
            call("POST", f"/backtests/{bid}/stop", {}, retries=1)
            stopped = True
        sleep(POLL_SECONDS)
    raise SystemExit(f"job still unresolved after "
                     f"{POLL_LIMIT * POLL_SECONDS}s: {bid}")


def _ensure(path):
    os.makedirs(path, exist_ok=True)
    return path


def main(argv=None, call=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=sorted(VARIANTS))
    parser.add_argument("--window", required=True)
    args = parser.parse_args(argv)
    if args.window == "all-bear":
        tags = list(BEAR_ORDER)
    elif args.window == "all":
        tags = list(FULL_ORDER)
    elif args.window in WINDOWS:
        tags = [args.window]
    else:
        raise SystemExit(f"unknown window {args.window!r}; "
                         f"choose one of {sorted(WINDOWS)}, all-bear, all")
    if call is None:
        from _api import call as call  # noqa: PLW0127
    for tag in tags:
        result = run_window(call, args.variant, tag)
        try:
            ret = float(result["summary"].get("pnl_percent"))
        except (TypeError, ValueError):
            ret = float("nan")
        if WINDOWS[tag][0] == "bear" and not ret > 0:
            print(f"BEAR GATE FAILED: {args.variant} {tag} {ret:+.2f}%. "
                  "Variant ends here.", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
