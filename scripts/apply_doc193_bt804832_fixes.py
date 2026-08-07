#!/usr/bin/env python3
"""Enable the two default-OFF levers shipped for the bt 804832 findings.

Both live in ``strategies[0].config`` — the OPERATIVE block. The top-level
``config`` on this document is legacy/near-empty and the broker never reads it;
patching there is the mistake that made three prior sessions predict the wrong
thresholds.

Levers
------
propagation_min_paths_conviction_bypass_raw : 1.0
    The min-paths quality filter is a CORROBORATION test, but `raw` saturates at
    ±1 (clamped aggregate over an integer sentiment seed), so one strong path
    already pins the ceiling and the filter starts deleting the top of the
    distribution instead of the bottom. On bt 804832 it fired 47 times and 46 of
    those (97.9%) carried raw > 1.000 — mean 1.335, max 1.800 — including the
    run's biggest winner on its single highest-scoring bar (raw=1.482). 1.0 = at
    or above saturation only, so a marginal name can never bypass.

backfill_queue_recheck_entry_extension : True
    The entry-extension gate is evaluated at scoring time; a queued item is
    bought bars later against a moved price, so a name the gate refused could
    still be admitted. VTYX was the run's worst entry exactly this way:
    extension-blocked (+77.4% > 25%) AND rank-band rejected (#27 vs #20), then
    bought via the queue 19 days after the gap that was the whole move, with
    max_return_so_far = 0.865%. A queue that overrides the gate makes the gate
    advisory.

Writes a timestamped backup next to the other scripts/doc*_backup_*.json files
before touching anything, and re-reads to verify.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/pranavkrishna/.claude/jobs/126c171e/tmp")
from rdb import conn, r  # noqa: E402

DOC_ID = 193
SCRIPTS = Path(__file__).resolve().parent

PATCH = {
    "propagation_min_paths_conviction_bypass_raw": 1.0,
    "backfill_queue_recheck_entry_extension": True,
}


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

    r.table("Strategies").get(doc["id"]).update(
        {"strategies": strategies}).run(c)

    # Verify by re-reading, not by trusting the write.
    back = list(r.table("Strategies").filter({"id": doc["id"]}).run(c))[0]
    live = (back.get("strategies") or [{}])[0].get("config", {})
    ok = True
    for key, value in PATCH.items():
        got = live.get(key)
        flag = "OK " if got == value else "FAIL"
        if got != value:
            ok = False
        print(f"  verify {flag} {key} = {got!r}")
    # These must be untouched — they are what the code fixes act on.
    for key in ("max_positions", "turnover_budget_monthly_pct",
                "entry_extension_block_pct", "core_target_pct",
                "nexus_portfolio_pct", "propagation_min_paths"):
        print(f"  unchanged {key} = {live.get(key)!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
