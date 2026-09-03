#!/usr/bin/env python3
"""Create the lab Strategies doc (bil25 EB + outlier sleeve) and the backtest-only
lab instance. Idempotent: re-running finds the existing rows by name/id and
restores the sleeve's default config on the lab doc.

    python3 scripts/outlier_lab_setup.py

Never touches doc 200 or instance strategy-eb (it only READS doc 200 to copy
the champion's strategy_eb lane verbatim).
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
from _api import call  # noqa: E402

DOC_NAME = "Strategy EB + Outlier Sleeve"
INSTANCE_ID = "strategy-eb-lab"
CHAMPION_DOC = "200"
STOCKS = ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"]


def _strategy_rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def main():
    from outlier_sleeve import DEFAULTS as OS
    _, champ = call("GET", f"/strategies/{CHAMPION_DOC}")
    eb = next(l for l in champ["strategies"] if l["strategy"] == "strategy_eb")
    assert eb["config"]["trend_off_book"] == {"GLD": 0.375, "GDX": 0.1875, "XLE": 0.1875}, \
        "doc 200 is not bil25"
    sleeve = {"strategy": "outlier_sleeve", "weight": 1.0, "execution_position": 20,
              "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
              "config": {**OS, "outlier_sleeve_enabled": True}}
    payload = {"name": DOC_NAME, "strategies": [json.loads(json.dumps(eb)), sleeve]}
    _, docs = call("GET", "/strategies")
    existing = next((d for d in _strategy_rows(docs) if d.get("name") == DOC_NAME), None)
    if existing:
        doc_id = str(existing["id"])
        call("PUT", f"/strategies/{doc_id}", payload)
        print("lab doc updated:", doc_id)
    else:
        _, created = call("POST", "/strategies", payload)
        doc_id = str(created.get("id") or created.get("strategy_id") or created.get("new_id"))
        print("lab doc created:", doc_id)
    code, inst = _safe_get(f"/instances/{INSTANCE_ID}")
    if code == 404 or not inst:
        _, brok = call("GET", "/instances/strategy-eb")
        body = {"id": INSTANCE_ID, "name": "Strategy EB lab (backtest only)",
                "strategy_id": doc_id, "granularity_time_increment": 86400,
                "brokerage_id": brok.get("brokerage_id"), "stocks": STOCKS}
        call("POST", "/instances", body)
        print("created instance", INSTANCE_ID)
    else:
        call("PATCH", f"/instances/{INSTANCE_ID}", {"strategy_id": doc_id})
        print("instance exists; strategy_id set to", doc_id)
    for s in STOCKS:
        try:
            call("POST", f"/instances/{INSTANCE_ID}/stocks", {"symbol": s})
        except BaseException:
            pass
    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    print("instance:", {k: check.get(k) for k in ("id", "strategy_id", "runCommand",
                                                   "granularity_time_increment")})
    return 0


def _safe_get(path):
    try:
        return call("GET", path)
    except BaseException as ex:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(ex) else 500), None


if __name__ == "__main__":
    raise SystemExit(main())
