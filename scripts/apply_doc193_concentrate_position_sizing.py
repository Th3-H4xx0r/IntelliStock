#!/usr/bin/env python3
"""Concentrate the satellite budget into fewer, larger positions on doc-193.

Lives in ``strategies[0].config`` — the OPERATIVE block. The top-level
``config`` is legacy (it still says max_positions=50 / allocation_profile=
balanced) and the broker never reads it; patching there is the mistake that
made three prior sessions predict the wrong thresholds.

Levers
------
total_spend_cap_concentrate : True
total_spend_cap_target_weight_pct : 0.12
    The V31.2 total-spend cap scaled EVERY new-entry buy down uniformly, so the
    more good ideas the book had, the less each one could pay. bt 496659 opened
    with the satellite's $2,280 (its 38% design share) spread across 5 sized
    names: all scaled by 0.812 to ~$456 = 7.6% of NAV. The objective measures
    the live book at mean 6.75% / median 4.73% against a 10-15% target, and
    states the whole thesis: "a +100% name at a 2% position is noise; at 10-15%
    it is the year".

    Concentrate ranks candidates by conviction and funds from the top at
    12% of NAV until the budget is spent, dropping the rest to the backfill
    queue (they can still enter later or via rotation). On the 496659 opening
    bar that is 3 names at $720 each instead of 5 at $456.

    The floor is the TARGET WEIGHT, not min_position_size - otherwise the tail
    of the budget buys a 2%-of-NAV runt, which is the same noise in a new form.
    Leftover stays as cash for the queue or a rotation.

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
    "total_spend_cap_concentrate": True,
    "total_spend_cap_target_weight_pct": 0.12,
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
                "allocation_max_new_stock_buys",
                "max_sector_portfolio_pct", "momentum_position_size_floor_pct",
                "residual_sleeve_symbol", "residual_sleeve_enabled"):
        print(f"  unchanged {key} = {live.get(key)!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
