#!/usr/bin/env python3
"""Re-weight doc-193: SPY core 30-40%, the rest in stocks; SQQQ majority in a bear.

The book was 62% index / 38% stocks by design, so five names could only be 7.6%
of NAV each. The objective wants the opposite shape: "a +100% name at a 2%
position is noise; at 10-15% it is the year."

Core / satellite
----------------
regime_profiles.{bull,chop,recovery}.core_target_pct : 0.6 -> 0.35
    satellite_design_share = 1 - core_target_pct - cash_reserve_floor_pct
                           = 1 - 0.35 - 0.02 = 0.63
    All three ENABLED profiles carry the target; there is deliberately no bear
    profile (that keeps the core OFF in a bear so the SQQQ hedge runs), so the
    bear path is untouched by this.

core_max_pct : 0.40
    The core is a RESIDUAL - core = clamp(1 - cash_floor - satellite, min, max).
    On the tick that builds the book the satellite is still 0, so the residual
    resolves to 0.98 and the core can grab the whole book before a single stock
    is bought. That is exactly what bt 823150 did:
        [core] bought $5844.93 SPY (band_deploy: 0.0% -> 98.0% of NAV)
    A hard 0.40 ceiling is what actually enforces "SPY is only 30-40%".

core_min_pct : 0.25
    satellite_max_share = 1 - 0.25 - 0.02 = 0.73, so a high-conviction name
    still has a band above the 0.63 design share to be funded into.

Position sizing
---------------
min_position_nav_pct : 0.06
    A new entry must be worth a max_positions slot. bt 498816 gave two of five
    alpha slots to AMD ($94, 1.6%) and KLAC ($100, 1.7%) and then refused SNDK
    three times on `MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)`.

total_spend_cap_target_weight_pct : 0.12 -> 0.14
momentum_position_size_floor_pct  : 0.12 -> 0.14
    With a 0.63 satellite, 4-5 names at 14% fills the sleeve. Both the opening
    allocator and the rotation top-up must agree or entries arrive at different
    sizes depending on which lane found the name.

Bear
----
residual_sleeve_bear_alloc_pct : 0.35 -> 0.70
    "if it's a bear market then SQQQ should be the majority position like 70-30".
    The leg already scaled 0.35 -> 0.70 with drawdown DEPTH, so a shallow bear
    only ever got 35%. This starts it at the majority weight.

    NOTE THE LEVERAGE: SQQQ is -3x, so 70% of NAV is ~210% effective short
    exposure. The leg's 10% stop and 10%/5% trail are the only things bounding
    it, and the objective records the bear leg as BUILT BUT NEVER SHOWN TO
    PROFIT IN A BEAR. This must be validated on a real bear window before it
    goes anywhere near real money.

Writes a timestamped backup before touching anything, and re-reads to verify.
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

TOP_LEVEL = {
    "core_max_pct": 0.40,
    "core_min_pct": 0.25,
    "min_position_nav_pct": 0.06,
    "total_spend_cap_target_weight_pct": 0.14,
    "momentum_position_size_floor_pct": 0.14,
    "residual_sleeve_bear_alloc_pct": 0.70,
}
PROFILE_PATCH = {"core_target_pct": 0.35}
PROFILES = ("bull", "chop", "recovery")


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

    strategies = doc.get("strategies") or []
    if not strategies or not isinstance(strategies[0], dict):
        print("!! strategies[0] missing - refusing to patch")
        return 1
    cfg = strategies[0].get("config")
    if not isinstance(cfg, dict):
        print("!! strategies[0].config missing - refusing to patch")
        return 1

    print(f"operative block has {len(cfg)} keys")
    for key, value in TOP_LEVEL.items():
        print(f"  {key}: {cfg.get(key)!r} -> {value!r}")
        cfg[key] = value

    profiles = cfg.get("regime_profiles")
    if not isinstance(profiles, dict):
        print("!! regime_profiles missing - refusing to patch")
        return 1
    if "bear" in profiles:
        print("!! a BEAR regime profile exists - refusing. doc-193 has none on "
              "purpose: it keeps the core OFF in a bear so the SQQQ hedge runs.")
        return 1
    for name in PROFILES:
        over = profiles.get(name)
        if not isinstance(over, dict):
            print(f"!! regime_profiles.{name} missing - refusing to patch")
            return 1
        if not over.get("core_sleeve_enabled"):
            print(f"   skip {name}: core_sleeve_enabled is not True")
            continue
        for key, value in PROFILE_PATCH.items():
            print(f"  regime_profiles.{name}.{key}: {over.get(key)!r} -> {value!r}")
            over[key] = value

    r.table("Strategies").get(doc["id"]).update({"strategies": strategies}).run(c)

    back = list(r.table("Strategies").filter({"id": doc["id"]}).run(c))[0]
    live = (back.get("strategies") or [{}])[0].get("config", {})
    ok = True
    for key, value in TOP_LEVEL.items():
        got = live.get(key)
        if got != value:
            ok = False
        print(f"  verify {'OK ' if got == value else 'FAIL'} {key} = {got!r}")
    lp = live.get("regime_profiles") or {}
    for name in PROFILES:
        got = (lp.get(name) or {}).get("core_target_pct")
        want = PROFILE_PATCH["core_target_pct"]
        if (lp.get(name) or {}).get("core_sleeve_enabled") and got != want:
            ok = False
        print(f"  verify {name}.core_target_pct = {got!r}")

    cash = float(live.get("cash_reserve_floor_pct", 0.02) or 0.02)
    tgt = PROFILE_PATCH["core_target_pct"]
    print("")
    print(f"  => satellite design share = 1 - {tgt} - {cash} = {1 - tgt - cash:.2f}")
    print(f"  => satellite max share    = 1 - {live.get('core_min_pct')} - {cash} = "
          f"{1 - float(live.get('core_min_pct')) - cash:.2f}")
    print(f"  => core hard ceiling      = {live.get('core_max_pct')}")

    for key in ("max_positions", "core_sleeve_enabled", "turnover_budget_monthly_pct",
                "satellite_conviction_overflow_min_raw_score", "min_position_size",
                "residual_sleeve_bear_alloc_max_pct", "residual_sleeve_bear_symbol",
                "residual_sleeve_bear_stop_loss_pct"):
        print(f"  unchanged {key} = {live.get(key)!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
