#!/usr/bin/env python3
"""Create the Strategy HX lab document and its backtest-only instance.

    python3 scripts/hx_lab_setup.py

Idempotent: re-running finds the existing rows by name/id and PUTs the lane
back to enabled DEFAULTS. Docs 200 (the live champion) and 201 (the EB lab)
are REFUSED outright — a killed runner has already left 201 holding a
candidate's config once, and a control that reproduces a candidate to the
decimal is a contaminated doc.
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from strategy_hx import DEFAULTS, strategy_hx_universe  # noqa: E402

DOC_NAME = "Strategy HX lab"
INSTANCE_ID = "strategy-hx-lab"
CLONE_FROM = "strategy-eb"
PROTECTED_DOC_IDS = frozenset({"200", "201"})
#: The default universe plus the benchmark. Variant bear books add SQQQ, SH
#: and GLD; the controller passes those per POST, and the broker fetch loop
#: adds them from the lane config regardless of this list.
STOCKS = sorted(set(strategy_hx_universe(DEFAULTS)) | {"SPY"})


def assert_writable(doc_id):
    """Refuse doc 200 and doc 201, in every string/int spelling."""
    if str(doc_id).strip() in PROTECTED_DOC_IDS:
        raise SystemExit(
            f"REFUSING to write doc {doc_id}: 200 is the live champion and "
            "201 is the EB lab. HX runs in its own document or not at all.")
    return doc_id


def lane(cfg) -> dict:
    return {"strategy": "strategy_hx", "weight": 1.0,
            "execution_position": 10, "decision_phase": "pre",
            "execution_scope": "run_once", "conditions": {},
            "config": {**cfg, "strategy_hx_enabled": True}}


def doc_payload(cfg) -> dict:
    return {"name": DOC_NAME, "strategies": [lane(cfg)]}


def _rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def main(call=None) -> int:
    if call is None:
        from _api import call as call  # noqa: PLW0127
    payload = doc_payload(DEFAULTS)

    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == DOC_NAME),
                    None)
    if existing:
        doc_id = assert_writable(existing["id"])
        call("PUT", f"/strategies/{doc_id}", payload)
        print("lab doc updated:", doc_id)
    else:
        _, created = call("POST", "/strategies", payload)
        doc_id = assert_writable(
            created.get("id") or created.get("strategy_id")
            or created.get("new_id"))
        print("lab doc created:", doc_id)

    code, inst = _safe_get(call, f"/instances/{INSTANCE_ID}")
    if code == 404 or not inst:
        _, broker_row = call("GET", f"/instances/{CLONE_FROM}")
        body = {"id": INSTANCE_ID, "name": "Strategy HX lab (backtest only)",
                "strategy_id": doc_id, "granularity_time_increment": 86400,
                "brokerage_id": broker_row.get("brokerage_id"),
                "stocks": list(STOCKS)}
        call("POST", "/instances", body)
        print("created instance", INSTANCE_ID)
    else:
        call("PATCH", f"/instances/{INSTANCE_ID}", {"strategy_id": doc_id})
        print("instance exists; strategy_id set to", doc_id)

    for symbol in STOCKS:
        try:
            call("POST", f"/instances/{INSTANCE_ID}/stocks",
                 {"symbol": symbol})
        except BaseException:
            # Already listed. The API 4xxs on a duplicate and _api.call turns
            # that into SystemExit, which is not a failure of this script.
            pass

    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    print("instance:", json.dumps(
        {k: check.get(k) for k in ("id", "strategy_id", "runCommand",
                                   "granularity_time_increment")}))
    return 0


def _safe_get(call, path):
    try:
        return call("GET", path)
    except BaseException as error:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(error) else 500), None


if __name__ == "__main__":
    raise SystemExit(main())
