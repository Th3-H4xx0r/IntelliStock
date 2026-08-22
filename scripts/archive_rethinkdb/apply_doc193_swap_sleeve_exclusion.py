#!/usr/bin/env python3
"""Exclude the index-core leg from the portfolio-swap candidate pool on doc-193.

Lives in ``strategies[0].config`` — the OPERATIVE block. The top-level
``config`` is legacy (it still says max_positions=50 / allocation_profile=
balanced) and the broker never reads it; patching there is the mistake that
made three prior sessions predict the wrong thresholds.

Lever
-----
momentum_swap_exclude_sleeve_legs : True
    `_mw_open_set` carries the broker sleeve's own legs, so SPY entered the V31.1
    portfolio-swap weakest-candidate pool. A low-beta index against a rising
    satellite sorts weakest almost every bar, so on bt 823150 the swap kept
    proposing to sell the ENTIRE $4,063 core (65% of NAV) to buy one name:

        V31.7 portfolio_swap weakest candidates: SPY(pnl=+1.8%,d=11,...)
        ROTATION PREVALIDATE sector-cap: skip incoming SNDK
            (sector 'technology' $4,601 > 40% cap $2,481) - swap skipped, keeping SPY

    $4,601 = ON $444 + AMD $94 + a $4,063 buy. Checked against Neo4j: SPY has no
    Company node, so it classifies 'unknown' - the buy SIZE breached the cap, not
    SPY's sector. SNDK signalled repeatedly from $388 and was never bought.

    Both branches are wrong. Blocked, the winner is refused; fired, the core is
    liquidated wholesale into one name straight through `core_min_pct`. The core
    already has a bounded way to fund conviction (the floor-limited overflow).

    Excluding the legs also fixes sizing: the pool falls back to a real alpha
    position and `momentum_position_size_floor_pct` tops the buy up to ~10% of
    NAV - the objective's target - instead of 65%.

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
    "momentum_swap_exclude_sleeve_legs": True,
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
                "max_sector_portfolio_pct", "momentum_position_size_floor_pct",
                "residual_sleeve_symbol", "residual_sleeve_enabled"):
        print(f"  unchanged {key} = {live.get(key)!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
