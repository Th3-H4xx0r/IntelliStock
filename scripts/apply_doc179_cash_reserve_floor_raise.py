"""Raise cash_reserve_floor_pct on prod Strategies doc 179 ("Nexus Only").

MERGE-ONLY: reads the full doc, mutates ONLY the single target key inside
doc['strategies'][0]['config'], and writes the strategies field back —
every other key (incl. plaintext secrets) and every other top-level field
is preserved.

Why: pre-live safety sweep (Agent 3) found cash_reserve_floor_pct=0.01
is too aggressive for $6.4K live deployment. At 1% the floor is only $64,
which leaves no headroom for adverse fills, partial fills, or T+1-style
edge cases. Raising to 5% gives ~$321 buffer on a $6.4K account.

Doc 179 is SHARED across instances main / nexus-live / nexus-testing.
This write affects all three. Risk: minimal — the floor only blocks
buys, never affects existing positions. nexus-live (Alpaca paper) is
unaffected behaviorally; nexus-testing (Alpaca live) gets the same
conservative bump.

Read-only by default. Pass --apply to write.

Usage:
  python scripts/apply_doc179_cash_reserve_floor_raise.py            # dry-run
  python scripts/apply_doc179_cash_reserve_floor_raise.py --apply    # write
  python scripts/apply_doc179_cash_reserve_floor_raise.py --rollback --apply
                                                                     # restore 0.01
"""
from __future__ import annotations

import argparse
import os
import sys

# Auto-load .env so RETHINKDB_HOST + INTELLISTOCK_CRED_KEY come from the
# operator's local config (mirrors backend/api/main.py:15-17).
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

# key -> (expected_current, target). expected_current is advisory: a
# mismatch warns but does NOT block setting the target. The dry-run
# prints the real current values regardless.
CHANGES_APPLY: dict[str, tuple[object, object]] = {
    "cash_reserve_floor_pct": (0.01, 0.05),
}

CHANGES_ROLLBACK: dict[str, tuple[object, object]] = {
    "cash_reserve_floor_pct": (0.05, 0.01),
}


def _connect():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r, r.connect(host=host, port=port, db=DB_NAME, timeout=15), host, port


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument(
        "--rollback",
        action="store_true",
        help="restore cash_reserve_floor_pct to 0.01 (the original aggressive value)",
    )
    args = ap.parse_args()

    changes = CHANGES_ROLLBACK if args.rollback else CHANGES_APPLY
    verb = "ROLLBACK" if args.rollback else "APPLY"

    try:
        r, conn, host, port = _connect()
    except Exception as e:
        print(f"ERROR: rethinkdb connect failed: {e}", file=sys.stderr)
        return 2

    print(f"[connected] {host}:{port} db={DB_NAME}")
    try:
        doc = r.table("Strategies").get(DOC_ID).run(conn)
        if doc is None:
            print(f"ERROR: Strategies.get({DOC_ID}) returned None", file=sys.stderr)
            return 3

        strategies = doc.get("strategies") or []
        if not strategies or not isinstance(strategies, list):
            print(f"ERROR: doc {DOC_ID} has no strategies[] list", file=sys.stderr)
            return 4
        cfg = strategies[0].get("config") or {}
        if not isinstance(cfg, dict):
            print(f"ERROR: strategies[0].config is not a dict", file=sys.stderr)
            return 4

        print(f"\nDoc {DOC_ID} ({doc.get('name')!r}) — config has {len(cfg)} keys")
        print(f"\n{verb} plan:")
        print(f"  {'key':<35} {'current':<12} -> {'target'}")
        for k, (expected, target) in changes.items():
            cur = cfg.get(k, "<ABSENT>")
            marker = ""
            if expected != "<ABSENT>" and cur != expected and cur != "<ABSENT>":
                marker = "  DRIFT"
            print(f"  {k:<35} {cur!r:<12} -> {target!r}{marker}")

        if not args.apply:
            print("\nDRY RUN. Re-run with --apply to write.")
            return 0

        # Mutate in place then write the strategies field back
        for k, (_e, t) in changes.items():
            cfg[k] = t
        strategies[0]["config"] = cfg

        res = r.table("Strategies").get(DOC_ID).update({"strategies": strategies}).run(conn)
        print(f"\nWrite result: {res}")
        errors = int((res or {}).get("errors", 0) or 0)
        if errors > 0:
            print(f"\nERROR: rethinkdb reported {errors} errors", file=sys.stderr)
            return 5

        # Post-write verification
        post = r.table("Strategies").get(DOC_ID).run(conn) or {}
        post_cfg = (post.get("strategies") or [{}])[0].get("config", {}) or {}
        print("\nPost-write verification:")
        for k, (_e, t) in changes.items():
            actual = post_cfg.get(k, "<ABSENT>")
            ok = actual == t or (
                isinstance(t, float)
                and isinstance(actual, (int, float))
                and abs(actual - t) < 1e-9
            )
            mark = "OK" if ok else "MISMATCH"
            print(f"  {k:<35} actual={actual!r:<12} expected={t!r}  [{mark}]")
        print("\nDone. Restart the live broker daemon to pick up the new floor.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
