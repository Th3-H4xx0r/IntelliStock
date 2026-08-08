"""Apply the round-3 A/B measurement levers to prod Strategies doc 179
("Nexus Only") for the leak-fix backtests (PR #91).

MERGE-ONLY: reads the full doc, mutates ONLY the 4 target keys inside
doc['strategies'][0]['config'], and writes the strategies field back — every
other key (incl. plaintext secrets) and every other top-level field is
preserved.

Read-only by default. Pass --apply to write.

Usage:
  RETHINKDB_HOST=<host> python3 scripts/apply_doc179_round3_ab_levers.py            # dry-run
  RETHINKDB_HOST=<host> python3 scripts/apply_doc179_round3_ab_levers.py --apply    # write
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
    _load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass

DB_NAME = "IntelliStock"
DOC_ID = 179

# key -> (expected_current, target). All four are NEW levers from PR #91, so
# the expected pre-state is <ABSENT> (code defaults: false / 0.0 / 0.0 / 0.0).
CHANGES: dict[str, tuple[object, object]] = {
    "llm_sell_conviction_bypass_enabled": ("<ABSENT>", True),
    "slot_min_notional_pct": ("<ABSENT>", 1.5),
    "anchor_reinforce_target_pct": ("<ABSENT>", 12),
    "rotation_graph_gate_max_loss_pct": ("<ABSENT>", 5),
}

ROLLBACK_HINT = (
    "ROLLBACK: DELETE llm_sell_conviction_bypass_enabled, slot_min_notional_pct, "
    "anchor_reinforce_target_pct, rotation_graph_gate_max_loss_pct from doc-179 "
    "config (code defaults restore pre-PR-91 behavior: false/0/0/0)."
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
    print("-" * 64)
    print(f"{'knob':<42} {'current':>9} -> {'target':>7}")
    drift = []
    for key, (expected, target) in CHANGES.items():
        current = cfg.get(key, "<ABSENT>")
        flag = ""
        if current != expected:
            flag = "  <-- DRIFT (expected %r)" % (expected,)
            drift.append(key)
        print(f"{key:<42} {str(current):>9} -> {str(target):>7}{flag}")
    print("-" * 64)
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
        print(f"  {key:<42} = {got}   [{match}]")
    print(f"[confirm] config_keys now = {len(cfg2)} (was {len(cfg)})")
    print(ROLLBACK_HINT)
    conn.close()
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
