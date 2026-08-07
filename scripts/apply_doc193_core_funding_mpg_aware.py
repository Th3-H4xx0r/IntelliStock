#!/usr/bin/env python3
"""Enable the max_positions-aware core funding pre-pass on doc-193.

Lives in ``strategies[0].config`` — the OPERATIVE block. The top-level
``config`` is legacy (it still says max_positions=50 / allocation_profile=
balanced) and the broker never reads it; patching there is the mistake that
made three prior sessions predict the wrong thresholds.

Lever
-----
core_funding_max_positions_aware : True
    The index core sizes its funding release BEFORE the execution pass, off the
    buys the allocator approved this bar. The 2026-08-03 sweep capped that
    request at the satellite headroom so the core would "never sell core to fund
    a buy that cannot clear" — but headroom is not the only gate a buy must
    clear. bt 455506 measured MAX_POSITIONS_GATE refusing 65 of the 91
    `SATELLITE OVERFLOW` fires on the same tick (71%; zero filled), and the SPY
    core saw-toothed 6.13 -> 4.21 -> 5.80 -> 4.64 -> 5.49 -> 4.76 shares:
    $9,081 of post-initial gross notional for -1.37 shares of net change, i.e.
    8.8x notional per $1 actually allocated — on the one lane exempt from the
    turnover budget, while TURNOVER BUDGET BINDING pinned the book at 50-51% of
    a 50% budget and blocked 16 real candidates.

    With this on, the release replays the cap in execution order and funds only
    the buys that will actually emit. Planned full exits still free a slot, so a
    rotation's paired buy stays funded — that is how SNDK entered in 455506
    (sell RGEN -> buy SNDK) and it must not regress.

Deliberately NOT changed: max_positions stays 6. This fixes the FUNDING side of
the interaction, not the cap. Raising the cap is on the do-not-retry list
(latches breach auto-heal; dilutes the prize ~62%).

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
    "core_funding_max_positions_aware": True,
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
                "propagation_min_paths_conviction_bypass_raw"):
        print(f"  unchanged {key} = {live.get(key)!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
