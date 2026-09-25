#!/usr/bin/env python3
"""Create the swing-trader lab document and instance, or the paper instance.

    python3 scripts/swing_lab_setup.py --start 2021-07-01 --end 2026-09-18
    python3 scripts/swing_lab_setup.py --paper --brokerage-id <paper brokerage id>

Lab (default): doc "Swing trader lab" with one strategy_swing lane at enabled
SWING_DEFAULTS plus backtest_credit_pending_sell_proceeds (plan A-backtest:
an entry decided with an exit is otherwise sized before the exit's cash
exists), and the backtest-only instance "swing-lab" at daily granularity. Its
watchlist is every S&P member visible in [--start, --end] from
SwingIndexMembership (run scripts/build_swing_reference_data.py first), plus
SPY, QQQ and the defensive ETFs (spec §7). The lab carries ONLY the swing
lane: the simulator's bar hook runs after its pending-fill block, so another
lane's close-filled sell of a bracketed symbol would execute before a
same-session stop (and the wheel is inert in backtests anyway).

Backtesting the lab: run it at granularity 86400 (daily bars; the instance
is created at 86400, and the swing lane refuses sub-daily bars since its
SMA200 and RSI are regular-hours daily indicators). Start every backtest
window on a MONDAY: a run's first ticks begin at midnight UTC, so a window
that opens mid-week decides its first sessions one session stale until the
first weekend (pre-flight F6). --start below only sizes the watchlist; it
should be on or before the backtest window's Monday.

Paper (--paper): doc "Swing trader paper" with both lanes enabled, and the
instance "swing-paper" on --brokerage-id, which must be a PAPER Alpaca account
and must not be alpaca-main's. The instance is never started here, and
alpaca-main is only READ (to prove the brokerage differs and to clone its
granularity), never written.

Idempotent. Docs 200-203 are REFUSED outright: 200 is the live champion, 201
the EB lab, 202 and 203 the frontier research documents.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from swing_trader import refdata  # noqa: E402
from swing_trader.constants import (  # noqa: E402
    DEFENSIVE_UNIVERSE,
    SWING_DEFAULTS,
    WHEEL_DEFAULTS,
)
from swing_trader.universe import norm_symbol  # noqa: E402

LAB_DOC_NAME = "Swing trader lab"
LAB_INSTANCE_ID = "swing-lab"
PAPER_DOC_NAME = "Swing trader paper"
PAPER_INSTANCE_ID = "swing-paper"
LIVE_INSTANCE_ID = "alpaca-main"
CLONE_FROM = "strategy-eb"
PROTECTED_DOC_IDS = frozenset({"200", "201", "202", "203"})
BENCHMARKS = ("SPY", "QQQ")
DEFAULT_START = "2021-07-01"

#: `_http` formats an HTTPError as "HTTP <code> on <method> <url>\n<body>".
_DUPLICATE_CODES = ("400", "409", "422")


def _is_duplicate(error) -> bool:
    text = str(error).lower()
    return "already" in text and any(f"http {code}" in text for code in _DUPLICATE_CODES)


def assert_writable(doc_id):
    if str(doc_id).strip() in PROTECTED_DOC_IDS:
        raise SystemExit(
            f"REFUSING to write doc {doc_id}: docs 200-203 are the live champion, the "
            "EB lab and the frontier research documents. The swing port runs in its "
            "own documents or not at all.")
    return doc_id


def swing_lane(*, lab) -> dict:
    config = {**SWING_DEFAULTS, "strategy_swing_enabled": True}
    if lab:
        config["backtest_credit_pending_sell_proceeds"] = True
    return {"strategy": "strategy_swing", "weight": 1.0, "execution_position": 10,
            "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
            "config": config}


def wheel_lane() -> dict:
    return {"strategy": "strategy_wheel", "weight": 1.0, "execution_position": 20,
            "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
            "config": {**WHEEL_DEFAULTS, "strategy_wheel_enabled": True}}


def lab_payload() -> dict:
    return {"name": LAB_DOC_NAME, "strategies": [swing_lane(lab=True)]}


def paper_payload() -> dict:
    return {"name": PAPER_DOC_NAME, "strategies": [swing_lane(lab=False), wheel_lane()]}


def _extras() -> set:
    return set(BENCHMARKS) | {norm_symbol(s) for s in DEFENSIVE_UNIVERSE}


def lab_watchlist(store, start, end) -> list:
    """Members visible on some session in [start, end]: the list in effect at
    `start` (the latest change strictly before it), plus every change dated
    before `end` (one dated `end` is visible only after it)."""
    first = refdata.members_before(store, start)
    if first is None:
        raise SystemExit(f"no SwingIndexMembership row before {start}: run "
                         "scripts/build_swing_reference_data.py first")
    later = store.run(store.between(refdata.MEMBERSHIP_TABLE, f"SPX|{start}", f"SPX|{end}"))
    union = set(first) | {norm_symbol(s) for r in later for s in (r.get("members") or [])}
    return sorted(union | _extras())


def paper_watchlist() -> list:
    return sorted(_extras())


def _rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "accounts", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def _safe_get(call, path):
    try:
        return call("GET", path)
    except (SystemExit, Exception) as error:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(error) else 500), None


def assert_paper_brokerage(call, brokerage_id):
    """Refuse unless `brokerage_id` is provably a paper account that is not
    alpaca-main's. Returns alpaca-main's instance row."""
    code, live = _safe_get(call, f"/instances/{LIVE_INSTANCE_ID}")
    if not live:
        raise SystemExit(f"REFUSING: {LIVE_INSTANCE_ID} could not be read (HTTP {code}), so "
                         "this cannot prove the brokerage is not the real-money account")
    if str(live.get("brokerage_id") or "") == str(brokerage_id):
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} is {LIVE_INSTANCE_ID}'s — "
                         "the real-money account")
    _, listing = call("GET", "/brokerages")
    row = next((b for b in _rows(listing) if str(b.get("id")) == str(brokerage_id)), None)
    if row is None:
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} not found")
    if row.get("alpaca_paper") is not True:
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} is not marked as an Alpaca "
                         f"paper account (alpaca_paper={row.get('alpaca_paper')!r})")
    return live


def _upsert_doc(call, name, payload):
    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == name), None)
    if existing:
        doc_id = assert_writable(existing["id"])
        call("PUT", f"/strategies/{doc_id}", payload)
        print("doc updated:", doc_id)
        return doc_id
    _, created = call("POST", "/strategies", payload)
    doc_id = assert_writable(created.get("id") or created.get("strategy_id")
                             or created.get("new_id"))
    print("doc created:", doc_id)
    return doc_id


def _upsert_instance(call, instance_id, *, name, doc_id, granularity, brokerage_id, stocks):
    code, inst = _safe_get(call, f"/instances/{instance_id}")
    if code == 404 or not inst:
        # `granularity` is the field CreateInstanceBody reads (a string of
        # seconds); under any other name it is dropped and the instance is
        # created at the 60 s default.
        call("POST", "/instances", {"id": instance_id, "name": name,
                                    "strategy_id": int(doc_id), "granularity": granularity,
                                    "brokerage_id": brokerage_id, "stocks": list(stocks)})
        print("created instance", instance_id)
    else:
        # PATCH /instances/{id} drops strategy_id; link-strategy is the route.
        call("POST", f"/instances/{instance_id}/link-strategy", {"strategy_id": int(doc_id)})
        print("instance exists; strategy linked to", doc_id)
    for symbol in stocks:
        try:
            call("POST", f"/instances/{instance_id}/stocks", {"symbol": symbol})
        except SystemExit as error:
            if not _is_duplicate(error):
                raise
    _, check = call("GET", f"/instances/{instance_id}")
    print("instance:", json.dumps({k: (check or {}).get(k) for k in (
        "id", "strategy_id", "runCommand", "granularity_time_increment", "brokerage_id")}))


def main(argv=None, *, call=None, store=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper", action="store_true",
                    help="create swing-paper (both lanes) instead of the backtest lab")
    ap.add_argument("--brokerage-id", help="the PAPER brokerage for --paper")
    ap.add_argument("--start", default=DEFAULT_START,
                    help="first day of the lab watchlist window (backtest windows "
                         "should start on a Monday on or after it)")
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat(),
                    help="last day of the lab watchlist window")
    args = ap.parse_args(argv)
    if call is None:
        from _api import call as call  # noqa: PLW0127

    if args.paper:
        if not args.brokerage_id:
            raise SystemExit("--paper needs --brokerage-id (a paper Alpaca account)")
        live = assert_paper_brokerage(call, args.brokerage_id)
        doc_id = _upsert_doc(call, PAPER_DOC_NAME, paper_payload())
        granularity = str(live.get("granularity_time_increment") or "60")
        _upsert_instance(call, PAPER_INSTANCE_ID, name="Swing trader (paper)",
                         doc_id=doc_id, granularity=granularity,
                         brokerage_id=args.brokerage_id, stocks=paper_watchlist())
        print("NOT started: link the conviction model on both lanes, then start it.")
        return 0

    if store is None:
        from db import store as db_store
        store = db_store
    stocks = lab_watchlist(store, args.start, args.end)
    print(f"lab watchlist: {len(stocks)} symbols for {args.start}..{args.end}", flush=True)
    start_day = date.fromisoformat(args.start)
    if start_day.weekday() != 0:
        print(f"note: --start {args.start} is a {start_day:%A}, not a Monday. Start the "
              "lab's backtest windows on a Monday (pre-flight F6), at granularity 86400.",
              flush=True)
    doc_id = _upsert_doc(call, LAB_DOC_NAME, lab_payload())
    code, inst = _safe_get(call, f"/instances/{LAB_INSTANCE_ID}")
    brokerage_id = None
    if code == 404 or not inst:
        _, clone = call("GET", f"/instances/{CLONE_FROM}")
        brokerage_id = (clone or {}).get("brokerage_id")
    _upsert_instance(call, LAB_INSTANCE_ID, name="Swing trader lab (backtest only)",
                     doc_id=doc_id, granularity="86400", brokerage_id=brokerage_id,
                     stocks=stocks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
