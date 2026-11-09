#!/usr/bin/env python3
"""Create the Strategy HT PAPER document and instance (TQQQ stepped 0.375).

    python3 scripts/ht_paper_setup.py

Spec: docs/superpowers/specs/2026-11-09-strategy-ht-paper-forward-test.md.
Idempotent. Refuses docs 200/201 and never touches alpaca-main or the F14
mirror's brokerage. The instance is created STOPPED; the operator starts it.
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from strategy_ht import DEFAULTS  # noqa: E402

DOC_NAME = "Strategy HT paper (TQQQ stepped 0.375)"
INSTANCE_ID = "strategy-ht-paper"
#: "Alpaca Paper" (PA3IBY5S84PG), the shared paper account; chosen by the operator 2026-11-09.
BROKERAGE_ID = "bf78ad0c-3073-4aac-97a5-a29c7b043404"
F14_MIRROR_BROKERAGE_ID = "1169c5e6-3308-438b-a6ed-406b55328d51"
PROTECTED_DOC_IDS = frozenset({"200", "201"})
INITIAL_VALUE = 10000.0
STOCKS = ["BIL", "QQQ", "TQQQ"]

PAPER_CONFIG = {
    **DEFAULTS,
    "strategy_ht_enabled": True,
    "filter_symbol": "QQQ", "one_x_symbol": "QQQ", "cash_symbol": "BIL",
    "in_symbol": "TQQQ",
    "vol_target": 0.375, "exp_min": 1.0, "exp_max": 2.0, "exp_grid": 0.5, "exp_hyst": 0.15,
    "enter_band": 0.01, "exit_band": 0.03, "bounce_drop": 0.0,
    "out_symbols": {"BIL": 1.0},
}


def assert_writable(doc_id):
    if str(doc_id).strip() in PROTECTED_DOC_IDS:
        raise SystemExit(f"REFUSING to write doc {doc_id}: 200 is the live champion and 201 is the EB lab.")
    return doc_id


def lane(cfg) -> dict:
    return {"strategy": "strategy_ht", "weight": 1.0, "execution_position": 10,
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


def main(call=None) -> int:
    if call is None:
        from _api import call as call  # noqa: PLW0127
    if BROKERAGE_ID == F14_MIRROR_BROKERAGE_ID:
        raise SystemExit("REFUSING: the F14 mirror's brokerage must not host another instance.")

    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == DOC_NAME), None)
    payload = {"name": DOC_NAME, "strategies": [lane(PAPER_CONFIG)]}
    if existing:
        doc_id = assert_writable(existing["id"]); call("PUT", f"/strategies/{doc_id}", payload); print("paper doc updated:", doc_id)
    else:
        _, created = call("POST", "/strategies", payload)
        doc_id = assert_writable(created.get("id") or created.get("strategy_id") or created.get("new_id")); print("paper doc created:", doc_id)

    code, inst = _safe(call, "GET", f"/instances/{INSTANCE_ID}")
    if code == 404 or not isinstance(inst, dict):
        call("POST", "/instances", {"id": INSTANCE_ID, "name": "Strategy HT paper (TQQQ stepped 0.375)",
                                    "strategy_id": doc_id, "granularity": "86400",
                                    "brokerage_id": BROKERAGE_ID, "stocks": list(STOCKS)})
        print("created instance", INSTANCE_ID)
    else:
        if str(inst.get("strategy_id")) != str(doc_id):
            call("POST", f"/instances/{INSTANCE_ID}/link-strategy", {"strategy_id": int(doc_id)})
            print("instance re-linked to", doc_id)
    # clean-room: the shared account's other books must not count as this instance's NAV
    call("PATCH", f"/instances/{INSTANCE_ID}", {"initial_value": INITIAL_VALUE, "clean_room_mode": True})
    for symbol in STOCKS:
        code, msg = _safe(call, "POST", f"/instances/{INSTANCE_ID}/stocks", {"symbol": symbol})
        if code in (404, 500) and "already" not in str(msg).lower():
            raise SystemExit(f"adding {symbol} failed: {msg}")

    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    print("instance:", json.dumps({k: check.get(k) for k in
                                   ("id", "strategy_id", "brokerage_id", "runCommand", "granularity_time_increment",
                                    "initial_value", "clean_room_mode", "stocks")}))
    print("NOT started. Start it from the Instances tab (or POST /instances/%s/start) when ready." % INSTANCE_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
