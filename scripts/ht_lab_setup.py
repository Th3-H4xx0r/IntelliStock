#!/usr/bin/env python3
"""Create the Strategy HT lab document and its backtest-only instance.

    python3 scripts/ht_lab_setup.py

Idempotent: re-running finds the existing rows and PUTs the lane back to
enabled DEFAULTS. Docs 200 (the live champion) and 201 (the EB lab) are
REFUSED outright. Pattern copied from scripts/hx_lab_setup.py.
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from strategy_ht import DEFAULTS, strategy_ht_universe  # noqa: E402

DOC_NAME = "Strategy HT lab"
INSTANCE_ID = "strategy-ht-lab"
CLONE_FROM = "strategy-eb"
PROTECTED_DOC_IDS = frozenset({"200", "201"})
STOCKS = sorted(set(strategy_ht_universe(DEFAULTS)) | {"SPY"})
_DUPLICATE_CODES = ("400", "409", "422")


def _is_duplicate(error) -> bool:
    text = str(error).lower()
    return "already" in text and any(f"http {code}" in text for code in _DUPLICATE_CODES)


def assert_writable(doc_id):
    if str(doc_id).strip() in PROTECTED_DOC_IDS:
        raise SystemExit(f"REFUSING to write doc {doc_id}: 200 is the live champion and "
                         "201 is the EB lab. HT runs in its own document or not at all.")
    return doc_id


def lane(cfg) -> dict:
    return {"strategy": "strategy_ht", "weight": 1.0, "execution_position": 10,
            "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
            "config": {**cfg, "strategy_ht_enabled": True}}


def doc_payload(cfg) -> dict:
    return {"name": DOC_NAME, "strategies": [lane(cfg)]}


def _rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def _safe_get(call, path):
    try:
        return call("GET", path)
    except BaseException as error:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(error) else 500), None


def main(call=None) -> int:
    if call is None:
        from _api import call as call  # noqa: PLW0127
    payload = doc_payload(DEFAULTS)

    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == DOC_NAME), None)
    if existing:
        doc_id = assert_writable(existing["id"])
        call("PUT", f"/strategies/{doc_id}", payload)
        print("lab doc updated:", doc_id)
    else:
        _, created = call("POST", "/strategies", payload)
        doc_id = assert_writable(created.get("id") or created.get("strategy_id") or created.get("new_id"))
        print("lab doc created:", doc_id)

    code, inst = _safe_get(call, f"/instances/{INSTANCE_ID}")
    if code == 404 or not inst:
        _, broker_row = call("GET", f"/instances/{CLONE_FROM}")
        # `granularity`, NOT `granularity_time_increment` (see hx_lab_setup.py).
        body = {"id": INSTANCE_ID, "name": "Strategy HT lab (backtest only)",
                "strategy_id": doc_id, "granularity": "86400",
                "brokerage_id": broker_row.get("brokerage_id"), "stocks": list(STOCKS)}
        call("POST", "/instances", body)
        print("created instance", INSTANCE_ID)
    else:
        call("POST", f"/instances/{INSTANCE_ID}/link-strategy", {"strategy_id": int(doc_id)})
        print("instance exists; strategy linked to", doc_id)

    for symbol in STOCKS:
        try:
            call("POST", f"/instances/{INSTANCE_ID}/stocks", {"symbol": symbol})
        except SystemExit as error:
            if not _is_duplicate(error):
                raise

    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    print("instance:", json.dumps({k: check.get(k) for k in
                                   ("id", "strategy_id", "runCommand", "granularity_time_increment", "stocks")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
