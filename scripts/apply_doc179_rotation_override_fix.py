"""Apply the rotation override recalibration (kimi-k2.5 fix) to prod Strategies
doc 179 ("Nexus Only").

MERGE-ONLY: reads the full doc, mutates ONLY the target keys inside
doc['strategies'][0]['config'], and writes the strategies field back — every
other key (incl. plaintext secrets) and every other top-level field is preserved.

Read-only by default. Pass --apply to write. DO NOT --apply until a cold kimi
backtest of this config has cleared the +113% baseline (operator backtest gate).

Usage:
  RETHINKDB_HOST=REDACTED-IP python scripts/apply_doc179_rotation_override_fix.py          # dry-run
  RETHINKDB_HOST=REDACTED-IP python scripts/apply_doc179_rotation_override_fix.py --apply   # write
"""
from __future__ import annotations

import argparse
import os
import sys

DB_NAME = "IntelliStock"
DOC_ID = 179

# key -> (expected_current, target). expected_current is advisory: a mismatch
# warns (config drifted / key absent) but does NOT block setting the target.
# The dry-run prints the real current values regardless.
CHANGES: dict[str, tuple[object, object]] = {
    # Recalibrate override floors to the 1.8 raw-score ceiling (current floors
    # are above the ceiling -> mathematically dead).
    "rotation_winner_lock_bypass_min_raw_score": (1.8, 1.5),
    "rotation_break_glass_raw_score": (3.50, 1.5),
    "rotation_break_glass_delta": (2.50, 1.0),
    "rotation_profitable_min_incoming_raw_score": (2.0, 1.5),
    # Activate the existing peak-drop escape from the profitable_min_hold block.
    "profitable_min_hold_release_enabled": (False, True),
    "profitable_min_hold_release_peak_drop_pct": (12.0, 8.0),
    # New conviction override (Part 2 code) for the profitable_min_hold branch.
    "profitable_min_hold_conviction_override_enabled": ("<ABSENT>", True),
    "profitable_min_hold_conviction_min_raw_score": ("<ABSENT>", 1.5),
    "profitable_min_hold_conviction_min_delta": ("<ABSENT>", 1.0),
    "profitable_min_hold_conviction_max_held_pnl_pct": ("<ABSENT>", 10.0),
}

ROLLBACK_HINT = (
    "ROLLBACK: set rotation_winner_lock_bypass_min_raw_score=1.8, "
    "rotation_break_glass_raw_score=3.50, rotation_break_glass_delta=2.50, "
    "rotation_profitable_min_incoming_raw_score=2.0, "
    "profitable_min_hold_release_enabled=False, "
    "profitable_min_hold_release_peak_drop_pct=12.0, and DELETE the four "
    "profitable_min_hold_conviction_* keys."
)


def _connect():
    from rethinkdb import RethinkDB

    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    conn = r.connect(host=host, port=port, timeout=15)
    return r, conn, host, port


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()

    r, conn, host, port = _connect()
    print(f"[connected] {host}:{port} db={DB_NAME}")

    doc = r.db(DB_NAME).table("Strategies").get(DOC_ID).run(conn)
    if not doc:
        print(f"[ERROR] Strategies doc {DOC_ID} not found")
        return 2
    strategies = doc.get("strategies") or []
    if not strategies or not isinstance(strategies[0], dict):
        print("[ERROR] doc['strategies'][0] missing/!dict")
        return 2
    cfg = strategies[0].get("config")
    if not isinstance(cfg, dict):
        print("[ERROR] doc['strategies'][0]['config'] missing/!dict")
        return 2

    print(f"[doc] id={doc.get('id')} name={doc.get('name')!r} config_keys={len(cfg)}")
    print(f"[mode] {'APPLY (write)' if args.apply else 'DRY-RUN (read-only)'}")
    print("-" * 72)
    print(f"{'knob':<48} {'current':>9} -> {'target':>7}")
    drift = []
    for key, (expected, target) in CHANGES.items():
        current = cfg.get(key, "<ABSENT>")
        flag = ""
        if current != expected:
            flag = "  <-- DRIFT (expected %r)" % (expected,)
            drift.append(key)
        print(f"{key:<48} {str(current):>9} -> {str(target):>7}{flag}")
    print("-" * 72)
    if drift:
        print(f"[warn] {len(drift)} knob(s) differ from the expected pre-state: {drift}")
        print("[warn] proceeding will still set the targets above.")

    if not args.apply:
        print("[dry-run] no write performed. Re-run with --apply to write.")
        print(ROLLBACK_HINT)
        conn.close()
        return 0

    for key, (_expected, target) in CHANGES.items():
        cfg[key] = target
    strategies[0]["config"] = cfg
    res = r.db(DB_NAME).table("Strategies").get(DOC_ID).update({"strategies": strategies}).run(conn)
    print(f"[write] update result: {res}")
    if res.get("errors"):
        print(f"[ERROR] update reported {res['errors']} error(s): {res.get('first_error')}; aborting before confirm.")
        conn.close()
        return 4

    doc2 = r.db(DB_NAME).table("Strategies").get(DOC_ID).run(conn)
    cfg2 = doc2["strategies"][0]["config"]
    print("[confirm] post-write values:")
    ok = True
    for key, (_expected, target) in CHANGES.items():
        got = cfg2.get(key, "<ABSENT>")
        match = "OK" if got == target else "MISMATCH"
        if got != target:
            ok = False
        print(f"  {key:<48} = {got}   [{match}]")
    print(f"[confirm] config_keys now = {len(cfg2)} (was {len(cfg)})")
    print(ROLLBACK_HINT)
    conn.close()
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
