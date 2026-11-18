#!/usr/bin/env python3
"""Sequential API controller for the Strategy Pairs runs.

    python3 scripts/pairs_run_native_api.py --basket reel --mode hold
    python3 scripts/pairs_run_native_api.py --basket reel --mode reversal
    python3 scripts/pairs_run_native_api.py --basket reel --mode zscore
    python3 scripts/pairs_run_native_api.py --basket reel --mode all

STRICTLY SEQUENTIAL (operator veto on parallel posts, 2026-09-03): refuse if
any job is on the engine, post ONE run, poll until it is terminal, archive it.
Every number comes back from the engine; nothing is simulated here.

`hold` is the control: the same pairs held 50/50. A candidate's edge is its
return minus the control's, which is half of the long/short pair trade.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from hx_run_native_api import (  # noqa: E402
    archive, drawdown_of, engine_busy,
)
from pairs_lab_setup import BASKETS, DOC_NAME, INSTANCE_ID  # noqa: E402
from hx_lab_setup import assert_writable  # noqa: E402
from strategy_pairs import DEFAULTS, MODES, strategy_pairs_universe  # noqa: E402

START, END = "2021-11-01", "2026-11-13"
INITIAL_CASH = 100000
# Seconds. Daily: the engine spends roughly 40 ms on every step whatever the
# strategy does, so a five-year run is ~2 minutes at daily steps and over an
# hour at 15-minute steps (BT 103624 was 33% done after 25 minutes).
GRANULARITY = os.environ.get("PAIRS_GRANULARITY", "86400")
POLL_SECONDS = 20
POLL_LIMIT = 1620          # 9 hours
OUT_ROOT = os.path.join(_ROOT, "output", "research", "pairs-2026-11-17")
ALL_ORDER = ("hold", "reversal", "zscore")
_DONE = ("finished", "completed", "error", "stopped", "failed")


def run_config(basket, mode, overrides=None) -> dict:
    return {**DEFAULTS, "strategy_pairs_enabled": True,
            "pairs": [list(p) for p in BASKETS[basket]], "mode": mode,
            **(overrides or {})}


def post_body(cfg, start, end) -> dict:
    # No cost override: the engine's own flat 23.2 bps per side applies to
    # every fill, control and candidate alike.
    return {"instance_id": INSTANCE_ID,
            "stocks": sorted(set(strategy_pairs_universe(cfg)) | {"SPY"}),
            "start_date": start, "end_date": end,
            "granularity": GRANULARITY, "initial_cash": INITIAL_CASH,
            "evidence_mode": "off"}


def apply_config(call, cfg) -> str:
    """PUT the run's config onto the lab doc. Returns the doc id."""
    _, inst = call("GET", f"/instances/{INSTANCE_ID}")
    doc_id = assert_writable(inst["strategy_id"])
    _, doc = call("GET", f"/strategies/{doc_id}")
    if doc.get("name") != DOC_NAME:
        raise SystemExit(f"doc {doc_id} is named {doc.get('name')!r}, not "
                         f"{DOC_NAME!r}. Refusing to write it.")
    lanes = [dict(lane) for lane in (doc.get("strategies") or [])]
    mine = [lane for lane in lanes if lane.get("strategy") == "strategy_pairs"]
    if len(mine) != 1:
        raise SystemExit(f"expected one strategy_pairs lane, saw {len(mine)}")
    mine[0]["config"] = cfg
    call("PUT", f"/strategies/{doc_id}", {"name": doc["name"],
                                          "strategies": lanes})
    return str(doc_id)


def _patient(call, *args, attempts=30, sleep=time.sleep, **kwargs):
    """A READ that survives a network blip. `_api.call` turns a timeout into
    SystemExit, which killed the first chain twenty minutes into a five-year
    run and left the job orphaned on the engine. Never used for the POST."""
    for attempt in range(attempts):
        try:
            return call(*args, **kwargs)
        except SystemExit as error:
            if "network error" not in str(error).lower() or attempt == attempts - 1:
                raise
            sleep(60)


def run_one(call, basket, mode, start, end, overrides=None, tag="",
            sleep=time.sleep, adopt=None) -> dict:
    cfg = run_config(basket, mode, overrides)
    body = post_body(cfg, start, end)
    if adopt is None:
        busy = _patient(engine_busy, call)
        if busy:
            raise SystemExit(f"engine occupied: {busy}. One job at a time.")
        apply_config(call, cfg)
    # Timestamped so a re-run never overwrites an earlier run's record.
    out_dir = os.path.join(
        OUT_ROOT, f"{basket}-{mode}{tag}-{time.strftime('%m%d-%H%M%S')}")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "request.json"), "w") as fh:
        json.dump({"body": body, "config": cfg}, fh, indent=1)
    # An uncertain POST is never retried: a duplicate would be a second job.
    if adopt is None:
        _, accepted = call("POST", "/backtests", body, retries=1)
        bid = accepted["id"]
        print(f"POSTED {basket} {mode}{tag} {bid}", flush=True)
    else:
        bid = adopt
        print(f"ADOPTED {basket} {mode}{tag} {bid}", flush=True)
    for _ in range(POLL_LIMIT):
        _, state = _patient(call, "GET", f"/backtests/{bid}/status")
        status = state.get("status")
        if status in _DONE:
            summary = _patient(archive, call, bid, out_dir)
            drawdown = drawdown_of(summary)
            dd = f"{drawdown * 100:.1f}%" if drawdown is not None else "n/a"
            print(f"DONE {basket} {mode}{tag} {bid} {status} | return "
                  f"{summary.get('pnl_percent')}% | maxDD {dd} | {out_dir}",
                  flush=True)
            return {"id": bid, "status": status, "summary": summary,
                    "out_dir": out_dir}
        sleep(POLL_SECONDS)
    raise SystemExit(f"job unresolved after {POLL_LIMIT * POLL_SECONDS}s: {bid}")


def main(argv=None, call=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--basket", required=True, choices=sorted(BASKETS))
    parser.add_argument("--mode", required=True,
                        choices=sorted(MODES) + ["all"])
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    parser.add_argument("--lookback", type=int)
    parser.add_argument("--adopt", help="poll and archive this already-posted "
                        "job id instead of posting (single mode only)")
    args = parser.parse_args(argv)
    if call is None:
        from _api import call as call  # noqa: PLW0127
    overrides, tag = {}, ""
    if args.lookback:
        overrides["lookback_bars"] = args.lookback
        tag = f"-L{args.lookback}"
    for mode in (ALL_ORDER if args.mode == "all" else (args.mode,)):
        run_one(call, args.basket, mode, args.start, args.end, overrides, tag,
                adopt=args.adopt if args.mode != "all" else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
