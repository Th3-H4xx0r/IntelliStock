#!/usr/bin/env python3
"""Create the Strategy Pairs lab document and its backtest-only instance.

    python3 scripts/pairs_lab_setup.py

Idempotent. Refuses docs 200 and 201 (see hx_lab_setup.py). The instance lists
every leg of every basket, because a leg the instance does not list has no
bars and is silently never traded.
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from hx_lab_setup import _is_duplicate, _rows, _safe_get, assert_writable  # noqa: E402
from strategy_pairs import DEFAULTS, REEL_PAIRS  # noqa: E402

DOC_NAME = "Strategy Pairs lab"
INSTANCE_ID = "strategy-pairs-lab"
CLONE_FROM = "strategy-eb"

#: Twin pairs the reel does not show: same industry, same customers.
EXTRA_PAIRS = [
    ["PG", "CL"], ["GS", "MS"], ["T", "VZ"], ["UNP", "CSX"],
    ["DUK", "SO"], ["PFE", "MRK"], ["CAT", "DE"], ["AXP", "COF"],
]
#: Pairs that are NOT twins (they trend apart); the offline study lost on these.
LOOSE_PAIRS = [["AMD", "NVDA"], ["MSFT", "AAPL"], ["SPY", "QQQ"], ["GLD", "GDX"]]
BASKETS = {
    "reel": REEL_PAIRS,
    "extra": EXTRA_PAIRS,
    "loose": LOOSE_PAIRS,
    "kopep": [["KO", "PEP"]],
}
STOCKS = sorted({leg for pairs in BASKETS.values() for pair in pairs
                 for leg in pair} | {"SPY"})


def lane(cfg) -> dict:
    return {"strategy": "strategy_pairs", "weight": 1.0,
            "execution_position": 10, "decision_phase": "pre",
            "execution_scope": "run_once", "conditions": {},
            "config": {**cfg, "strategy_pairs_enabled": True}}


def doc_payload(cfg) -> dict:
    return {"name": DOC_NAME, "strategies": [lane(cfg)]}


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
        doc_id = assert_writable(created.get("id") or created.get("strategy_id")
                                 or created.get("new_id"))
        print("lab doc created:", doc_id)

    code, inst = _safe_get(call, f"/instances/{INSTANCE_ID}")
    if code == 404 or not inst:
        _, broker_row = call("GET", f"/instances/{CLONE_FROM}")
        # `granularity`, NOT `granularity_time_increment` (see hx_lab_setup.py).
        body = {"id": INSTANCE_ID, "name": "Strategy Pairs lab (backtest only)",
                "strategy_id": doc_id, "granularity": "86400",
                "brokerage_id": broker_row.get("brokerage_id"),
                "stocks": list(STOCKS)}
        call("POST", "/instances", body)
        print("created instance", INSTANCE_ID)
    else:
        call("POST", f"/instances/{INSTANCE_ID}/link-strategy",
             {"strategy_id": int(doc_id)})
        print("instance exists; strategy linked to", doc_id)

    for symbol in STOCKS:
        try:
            call("POST", f"/instances/{INSTANCE_ID}/stocks", {"symbol": symbol})
        except SystemExit as error:
            if not _is_duplicate(error):
                raise

    _, check = call("GET", f"/instances/{INSTANCE_ID}")
    print("instance:", json.dumps(
        {k: check.get(k) for k in ("id", "strategy_id", "runCommand",
                                   "granularity_time_increment")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
