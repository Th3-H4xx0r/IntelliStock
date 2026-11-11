#!/usr/bin/env python3
"""Create the F14 paper-mirror strategy document and its dedicated instance.

    python3 scripts/f14_instance_setup.py            # create / update, leave stopped
    python3 scripts/f14_instance_setup.py --start    # ...and start it

The instance runs the `strategy_f14` lane (backend/strategies/strategy_f14.py)
on the "TMasters Swing" paper account PA3TRLBV6ZNZ, stored in IntelliStock as
the "Alpaca Crypto Paper" brokerage. The `f14-mirror` compose service must be in
its `signal` role (the compose default) or the lane has nothing to trade from.

Idempotent. Refuses docs 200/201 and the off-limits PA37JPXGU196 brokerage, and
never touches alpaca-main. Runbook: docs/runbooks/f14-paper-mirror.md
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

DOC_NAME = "F14 paper mirror (frozen forward test)"
INSTANCE_ID = "f14-paper"           # forward_mirror.DEFAULT_INSTANCE_ID: the signal service tolerates only this one
INSTANCE_NAME = "F14 paper mirror"
#: "Alpaca Crypto Paper" row = the operator's "TMasters Swing" account PA3TRLBV6ZNZ.
BROKERAGE_ID = "b0d7dc67-f12b-4959-83db-0d324c6bc517"
ACCOUNT_NUMBER = "PA3TRLBV6ZNZ"
OFF_LIMITS_BROKERAGE_ID = "1169c5e6-3308-438b-a6ed-406b55328d51"    # PA37JPXGU196
PROTECTED_DOC_IDS = frozenset({"200", "201"})
INITIAL_VALUE = 10000.0
#: Alpaca instances are clean-room regardless; setting it makes the launcher
#: refuse a missing `initial_value` up front instead of crash-looping.
CLEAN_ROOM = True
STOCKS = ["SPY", "IEF", "MTUM", "IWD", "DBMF"]

LANE_CONFIG = {
    "strategy_f14_enabled": True,
    "brokerage_id": BROKERAGE_ID,
    "account_number": ACCOUNT_NUMBER,
    "instance_id": INSTANCE_ID,
    "dry_run": False,
    "act_minutes": 25,
}


def assert_writable(doc_id):
    if str(doc_id).strip() in PROTECTED_DOC_IDS:
        raise SystemExit(f"REFUSING to write doc {doc_id}: 200 is the live champion and 201 is the EB lab.")
    return doc_id


def lane(cfg) -> dict:
    return {"strategy": "strategy_f14", "weight": 1.0, "execution_position": 10,
            "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": cfg}


def _rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def _safe(call, method, path, body=None):
    try:
        return call(method, path, body) if body is not None else call(method, path)
    except BaseException as error:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(error) else 500), str(error)


def main(call=None, argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if call is None:
        from _api import call as call  # noqa: PLW0127
    if BROKERAGE_ID == OFF_LIMITS_BROKERAGE_ID:
        raise SystemExit("REFUSING: PA37JPXGU196 is off limits.")

    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == DOC_NAME), None)
    payload = {"name": DOC_NAME, "strategies": [lane(LANE_CONFIG)]}
    if existing:
        doc_id = assert_writable(existing["id"])
        call("PUT", f"/strategies/{doc_id}", payload)
        print("strategy doc updated:", doc_id)
    else:
        _, created = call("POST", "/strategies", payload)
        doc_id = assert_writable(created.get("id") or created.get("strategy_id") or created.get("new_id"))
        print("strategy doc created:", doc_id)

    code, inst = _safe(call, "GET", f"/instances/{INSTANCE_ID}")
    if code == 404 or not isinstance(inst, dict):
        call("POST", "/instances", {"id": INSTANCE_ID, "name": INSTANCE_NAME, "strategy_id": doc_id,
                                    "granularity": "86400", "brokerage_id": BROKERAGE_ID, "stocks": list(STOCKS)})
        print("created instance", INSTANCE_ID)
    else:
        if str(inst.get("brokerage_id")) != BROKERAGE_ID:
            raise SystemExit(f"REFUSING: instance {INSTANCE_ID} exists on brokerage {inst.get('brokerage_id')}.")
        if str(inst.get("strategy_id")) != str(doc_id):
            call("POST", f"/instances/{INSTANCE_ID}/link-strategy", {"strategy_id": int(doc_id)})
            print("instance re-linked to", doc_id)
    call("PATCH", f"/instances/{INSTANCE_ID}", {"initial_value": INITIAL_VALUE, "clean_room_mode": CLEAN_ROOM})
    for symbol in STOCKS:
        code, msg = _safe(call, "POST", f"/instances/{INSTANCE_ID}/stocks", {"symbol": symbol})
        if code in (404, 500) and "already" not in str(msg).lower():
            raise SystemExit(f"adding {symbol} failed: {msg}")

    if "--start" in argv:
        call("POST", f"/instances/{INSTANCE_ID}/start")
        print("start requested")
    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    print("instance:", json.dumps({k: check.get(k) for k in
                                   ("id", "strategy_id", "brokerage_id", "runCommand", "granularity_time_increment",
                                    "initial_value", "clean_room_mode", "stocks")}))
    if "--start" not in argv:
        print("NOT started. Re-run with --start, or use the Instances tab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
