#!/usr/bin/env python3
"""Let a rotation paired buy spend its own funding sell on doc-193.

Lives in ``strategies[0].config`` — the OPERATIVE block. The top-level
``config`` is legacy (it still says max_positions=50 / allocation_profile=
balanced) and the broker never reads it; patching there is the mistake that
made three prior sessions predict the wrong thresholds.

Lever
-----
backtest_credit_sell_proceeds_enabled : True
    A rotation's paired buy must be able to spend its own funding sell.
    Execution is next-event, so a sell submitted while the 15:00 bar is
    processed fills at the 16:00 quote and its proceeds are not available to
    that bar's buy. broker.py assumed the emulator credited synchronously.

    bt 559864, 2026-01-13 - the book sold $1,845 (SPY $1,107.91 + EEM $737.54)
    and bought the winner with $125:

        Momentum portfolio swap: sell EEM (pnl=+2.6%) -> buy SNDK (score=1.013, $743)
        Buy gate inputs for SNDK: cash=$125.31 cash_per_trade=$742.82
                                  available=$125.31 -> PASS
        FILL BUY SNDK qty=0.31934420 price=392.371487

    SNDK was correctly sized at 12.4% of NAV by the swap and executed at 2.1%.
    The mechanism (buy_ceiling, 95% haircut, kill switch) already exists and is
    tested for live; this turns it on for backtest, where the funding sell is
    deterministic and in the same cycle.

Writes a timestamped backup next to the other scripts/doc*_backup_*.json files
before touching anything, and re-reads to verify.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path

from rethinkdb import RethinkDB

r = RethinkDB()

DOC_ID = 193
SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent

PATCH = {
    "backtest_credit_sell_proceeds_enabled": True,
}


def _load_env() -> None:
    env = REPO / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def conn(timeout: int = 30):
    _load_env()
    return r.connect(
        host=os.environ.get("RETHINKDB_HOST"),
        port=int(os.environ.get("RETHINKDB_PORT", 28015)),
        db=os.environ.get("RETHINKDB_DB", "IntelliStock"),
        timeout=timeout,
    )


def main() -> int:
    c = conn()
    rows = list(r.table("Strategies").filter({"id": DOC_ID}).run(c))
    if not rows:
        rows = list(r.table("Strategies").filter({"id": str(DOC_ID)}).run(c))
    if not rows:
        print(f"!! strategy doc {DOC_ID} not found")
        return 1
    doc = rows[0]

    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = SCRIPTS / f"doc{DOC_ID}_backup_patch_{ts}.json"
    backup.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(f"backup -> {backup.name}")

    strategies = doc.get("strategies") or []
    if not strategies or not isinstance(strategies[0], dict):
        print("!! strategies[0] missing — refusing to patch")
        return 1
    cfg = strategies[0].get("config")
    if not isinstance(cfg, dict):
        print("!! strategies[0].config missing — refusing to patch")
        return 1

    print(f"operative block has {len(cfg)} keys")
    for key, value in PATCH.items():
        print(f"  {key}: {cfg.get(key)!r} -> {value!r}")
        cfg[key] = value

    r.table("Strategies").get(doc["id"]).update({"strategies": strategies}).run(c)

    # Verify by re-reading, not by trusting the write.
    back = list(r.table("Strategies").filter({"id": doc["id"]}).run(c))[0]
    live = (back.get("strategies") or [{}])[0].get("config", {})
    ok = True
    for key, value in PATCH.items():
        got = live.get(key)
        if got != value:
            ok = False
        print(f"  verify {'OK ' if got == value else 'FAIL'} {key} = {got!r}")
    # These must be untouched — they are what the code fix acts on.
    for key in ("max_positions", "turnover_budget_monthly_pct",
                "entry_extension_block_pct", "core_min_pct",
                "satellite_conviction_overflow_min_raw_score",
                "core_funding_max_positions_aware",
                "momentum_swap_exclude_sleeve_legs", "min_position_size",
                "allocation_max_new_stock_buys", "total_spend_cap_concentrate",
                "total_spend_cap_target_weight_pct",
                "live_credit_sell_proceeds_enabled",
                "max_sector_portfolio_pct", "momentum_position_size_floor_pct",
                "residual_sleeve_symbol", "residual_sleeve_enabled"):
        print(f"  unchanged {key} = {live.get(key)!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
