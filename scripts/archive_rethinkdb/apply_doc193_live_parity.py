#!/usr/bin/env python3
"""Make doc-193 measure what LIVE would actually do, and drop an inert lever.

Two independent corrections, both from the 2026-08-08 production-readiness
research (docs/handoffs/2026-08-08-production-readiness-research.md).

1. backtest_credit_sell_proceeds_enabled -> False
   The lever is INERT in backtest and creates a live-vs-backtest divergence.
   It lifts the broker's sizing ceiling, but PortfolioEmulator.execute_signal
   re-clamps to real buying power (portfolio_emulator.py:1414-1423, and
   get_buying_power = _cash - withheld - reserved at :408-420). Measured on
   bt 498816, 2026-01-16:

       Buy gate inputs for SNDK: cash=$700.74 cash_per_trade=$755.47
                                 available=$1397.39 cash_to_use=$755.47
       FILL BUY SNDK qty=1.68975570 price=414.687474   = $700.72

   The fill equals CASH ON HAND, not cash_to_use. So it added nothing to the
   +15.04%. Live has no such clamp (broker.py:8309-8311 sizes
   quantity = cash_to_use/price), so keeping it on means LIVE PLACES LARGER
   ORDERS THAN THE BACKTEST MODELLED, and the gap grows with conviction. There
   is also no backtest kill switch: live_credit_sell_proceeds_enabled is read
   only under mode == MODE_LIVE.

   Making it "work" in backtest would mean weakening the emulator's T+1
   settlement model, whose own docstring says conflating settled and unsettled
   cash "is exactly how a backtest recycles capital faster than the live
   account does". We are not doing that to buy a bigger number.

2. LIVE-PARITY FLAGS.
   backend/live_mode_overrides.py merges LIVE_OVERRIDES on top of the doc at
   broker.py:10354-10360, unconditionally except for three user-overridable
   keys. So the config that produced +15.04% is NOT the config that would run
   live. Setting the live-effective values here makes the backtest model the
   live book:

     quality_filter_missing_metadata_policy  warn -> block
         The big one: it NARROWS the live buy universe. Any name the backtest
         bought on missing metadata would be refused live.
     portfolio_drawdown_halt_enabled         -> True   (+ halt_pct 10.0)
     max_positions_breach_auto_rotate        -> False
     private_entity_bridge_enabled           -> False
     break_glass_fresh_shield_enabled        -> True
     nexus_live_fail_closed_on_missing_mcap  -> True
     v32_convert_cooldown_window_utc         -> True
     analyst_panel_enabled                   -> False

   Expect this to LOWER the backtest return. That is the point: the previous
   number was measured on a configuration that cannot be run with real money.

Not fixed by config, still divergent (see the research doc): live session hours
01-17 vs backtest 05-20, live-only price-sanity buy reject, live-only watchdogs,
and live nexus_live_max_llm_calls_per_cycle=4.
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
    "backtest_credit_sell_proceeds_enabled": False,
    "quality_filter_missing_metadata_policy": "block",
    "portfolio_drawdown_halt_enabled": True,
    "portfolio_drawdown_halt_pct": 10.0,
    "max_positions_breach_auto_rotate": False,
    "private_entity_bridge_enabled": False,
    "break_glass_fresh_shield_enabled": True,
    "nexus_live_fail_closed_on_missing_mcap": True,
    "v32_convert_cooldown_window_utc": True,
    "analyst_panel_enabled": False,
}

# These live in the regime profiles too and would otherwise re-enable
# themselves per-regime, defeating the parity.
PROFILE_PATCH = {"portfolio_drawdown_halt_enabled": True}


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
        print(f"!! strategy doc {DOC_ID} not found")
        return 1
    doc = rows[0]

    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = SCRIPTS / f"doc{DOC_ID}_backup_patch_{ts}.json"
    backup.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(f"backup -> {backup.name}")

    cfg = (doc.get("strategies") or [{}])[0].get("config")
    if not isinstance(cfg, dict):
        print("!! strategies[0].config missing - refusing to patch")
        return 1
    print(f"operative block has {len(cfg)} keys")
    for key, value in PATCH.items():
        print(f"  {key}: {cfg.get(key)!r} -> {value!r}")
        cfg[key] = value

    profiles = cfg.get("regime_profiles")
    if isinstance(profiles, dict):
        for name, over in profiles.items():
            if not isinstance(over, dict):
                continue
            for key, value in PROFILE_PATCH.items():
                if key in over and over[key] != value:
                    print(f"  regime_profiles.{name}.{key}: {over[key]!r} -> {value!r}")
                    over[key] = value

    r.table("Strategies").get(doc["id"]).update(
        {"strategies": doc["strategies"]}).run(c)

    back = list(r.table("Strategies").filter({"id": doc["id"]}).run(c))[0]
    live = (back.get("strategies") or [{}])[0].get("config", {})
    ok = True
    for key, value in PATCH.items():
        got = live.get(key)
        if got != value:
            ok = False
        print(f"  verify {'OK ' if got == value else 'FAIL'} {key} = {got!r}")
    lp = live.get("regime_profiles") or {}
    for name, over in lp.items():
        if isinstance(over, dict) and "portfolio_drawdown_halt_enabled" in over:
            print(f"  verify {name}.portfolio_drawdown_halt_enabled = "
                  f"{over['portfolio_drawdown_halt_enabled']!r}")

    print("")
    for key in ("core_max_pct", "core_min_pct", "min_position_nav_pct",
                "total_spend_cap_target_weight_pct", "total_spend_cap_concentrate",
                "momentum_swap_exclude_sleeve_legs", "core_funding_max_positions_aware",
                "residual_sleeve_bear_alloc_pct", "max_positions"):
        print(f"  unchanged {key} = {live.get(key)!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
