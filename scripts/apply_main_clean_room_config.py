"""Apply clean_room_mode + initial_value to the Instances.main row.

MERGE-ONLY: reads the full Instances row, sets ONLY ``clean_room_mode`` and
``initial_value`` via a partial update, and leaves every other field
(brokerage_id, secret, strategy_id, max_usage, status, etc.) untouched.

Once applied, the next time the broker daemon boots for ``main`` it does
ALL the rest automatically (broker.py:~5285+):

  1. Reads clean_room_mode + initial_value from this row.
  2. Detects "first clean_room boot for this instance" via the LiveBootAudit
     table being empty for ``main`` (live_boot_setup.is_first_clean_room_boot)
     and auto-runs the per-instance cleanup (preserves backtest snapshots)
     so the operator no longer has to run clear_main_instance_lookback_state.py
     separately. Idempotent: subsequent boots skip the cleanup.
  3. Passes clean_room_mode=True + initial_value to the adapter factory;
     RobinhoodAdapter.__init__ runs the WAL classifier instead of adopting
     whatever positions sit in the Robinhood account.
  4. The migration detector at broker.py:5685+ auto-resets stale snapshot
     keys (_portfolio_drawdown_state, _sold_cooldown, _v32_*,
     _deployment_bar_index, etc.) when it detects the cached backtest peak
     mismatching live equity — clean_room_mode=true is the default trigger
     for auto-reset (operator can still force-disable with the env var
     LIVE_AUTO_RESET_ON_MIGRATION=false if they want to investigate).
  5. Writes a LiveBootAudit row marking this boot, which makes subsequent
     boots see >=1 audit row and skip the auto-cleanup.

So the FULL launch sequence after this script applies is just:

  INTELLISTOCK_CRED_KEY=<key> RH_DRY_RUN=true RETHINKDB_HOST=... \\
      python3 backend/broker.py --instance main

Read-only by default. Pass ``--apply`` to write. ``--initial-value N`` is
REQUIRED (the instance's starting cash in USD) — read it from your own
``inspect_broker_state.py`` run; nothing operator-specific is baked in here.

Usage:
  python3 scripts/apply_main_clean_room_config.py
    # dry-run with defaults
  python3 scripts/apply_main_clean_room_config.py --apply
    # write with defaults
  python3 scripts/apply_main_clean_room_config.py \\
    --instance main --initial-value 7000 --apply
    # custom value
"""
from __future__ import annotations

import argparse
import os
import sys

# Auto-load .env so RETHINKDB_HOST + INTELLISTOCK_CRED_KEY (the same the
# broker daemon reads) come from the operator's local config. Mirrors
# backend/api/main.py:15-17.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
    _load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass

DB_NAME = "IntelliStock"
DEFAULT_INSTANCE_ID = "main"
DEFAULT_INITIAL_VALUE = None  # operator must pass --initial-value; nothing baked in


# key -> (expected_current, target). expected_current is advisory: a mismatch
# warns but does NOT block setting the target. The dry-run prints the real
# current values regardless.
DEFAULT_CHANGES: dict[str, tuple[object, object]] = {
    "clean_room_mode": ("<ABSENT>", True),
    "initial_value": ("<ABSENT>", DEFAULT_INITIAL_VALUE),
}


ROLLBACK_HINT = (
    "ROLLBACK: re-run with --apply but use --rollback to DELETE the two "
    "fields (or just set them to: clean_room_mode=False and remove the "
    "initial_value field). To force-disable clean_room mode without "
    "deleting, run `RETHINKDB_HOST=... python3 scripts/apply_main_clean_room_config.py "
    "--rollback --apply`. Code path then falls through to legacy behavior."
)


def _connect():
    try:
        from rethinkdb import RethinkDB
    except ImportError:
        print("ERROR: rethinkdb driver not installed. `pip3 install rethinkdb`.", file=sys.stderr)
        sys.exit(2)
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    try:
        conn = r.connect(host=host, port=port, db=DB_NAME, timeout=10)
    except Exception as e:
        print(f"ERROR: RethinkDB connect({host}:{port}): {e}", file=sys.stderr)
        sys.exit(3)
    return r, conn


def _print_current_values(row: dict, changes: dict) -> tuple[bool, int]:
    """Print current vs target for each change. Return (any_drift, total)."""
    drift = False
    print("\nCurrent vs proposed:")
    print(f"  {'key':<28} {'current':<20} -> {'target':<10}")
    for k, (expected, target) in changes.items():
        cur = row.get(k, "<ABSENT>")
        cur_display = repr(cur) if cur != "<ABSENT>" else "<ABSENT>"
        target_display = repr(target)
        marker = ""
        if expected != "<ABSENT>" and cur != expected and cur != "<ABSENT>":
            marker = "  DRIFT"
            drift = True
        print(f"  {k:<28} {cur_display:<20} -> {target_display:<10}{marker}")
    return drift, len(changes)


def main(instance_id: str, apply: bool, initial_value: float, rollback: bool) -> int:
    r, conn = _connect()
    try:
        row = r.table("Instances").get(instance_id).run(conn)
        if row is None:
            print(f"ERROR: Instances.get({instance_id!r}) returned None — row not found.", file=sys.stderr)
            return 4

        # Build the changes dict for this run
        if rollback:
            # Set clean_room_mode=False; we do NOT delete initial_value here
            # because the broker-side resolution treats False as "off" and the
            # initial_value field is then ignored. If you want a clean delete,
            # use a separate one-off rethinkdb .replace() call.
            changes = {
                "clean_room_mode": ("<see current>", False),
            }
            verb = "ROLLBACK"
        else:
            if initial_value is None:
                print(
                    "ERROR: --initial-value is required (nothing is baked in). Read the "
                    "instance's starting cash from scripts/inspect_broker_state.py.",
                    file=sys.stderr,
                )
                return 6
            changes = dict(DEFAULT_CHANGES)
            changes["initial_value"] = ("<ABSENT>", float(initial_value))
            verb = "APPLY"

        # Friendly context
        print(f"Target: Instances.{instance_id}")
        print(f"  id            = {row.get('id')!r}")
        print(f"  brokerage_id  = {row.get('brokerage_id')!r}")
        print(f"  strategy_id   = {row.get('strategy_id')!r}")
        print(f"  status        = {row.get('status')!r}")
        print(f"  running       = {row.get('running')!r}")
        print(f"  max_usage     = {row.get('max_usage')!r}")
        print(f"\n{verb} plan:")
        _drift, n_changes = _print_current_values(row, changes)

        if not apply:
            print()
            print(f"DRY RUN. {n_changes} key(s) would be {verb.lower()}-set on Instances.{instance_id}.")
            print("Re-run with --apply to write.")
            return 0

        # Build the partial update payload
        update_payload = {k: t for k, (_e, t) in changes.items()}
        print(f"\nWriting: r.table('Instances').get({instance_id!r}).update({update_payload!r})")
        res = r.table("Instances").get(instance_id).update(update_payload).run(conn)
        print(f"\nWrite result: {res}")

        replaced = int((res or {}).get("replaced", 0) or 0)
        unchanged = int((res or {}).get("unchanged", 0) or 0)
        errors = int((res or {}).get("errors", 0) or 0)
        if errors > 0:
            print(f"\nERROR: rethinkdb reported {errors} error(s) during update. See above.", file=sys.stderr)
            return 5
        if replaced == 0 and unchanged == 0:
            print(
                f"\nWARN: rethinkdb reported neither replaced nor unchanged ({res}). "
                "Verify manually.",
                file=sys.stderr,
            )
        elif unchanged > 0 and replaced == 0:
            print("\nNote: values were already at the target — no row mutation needed.")

        # Post-write verification: re-read and confirm the fields stuck
        post = r.table("Instances").get(instance_id).run(conn) or {}
        print("\nPost-write verification:")
        for k, (_e, t) in changes.items():
            actual = post.get(k, "<ABSENT>")
            ok = (actual == t) or (
                isinstance(t, float) and isinstance(actual, (int, float)) and abs(actual - t) < 1e-6
            )
            mark = "OK" if ok else "MISMATCH"
            print(f"  {k:<28} actual={actual!r:<20} expected={t!r:<10}  [{mark}]")

        print(f"\nDone. {ROLLBACK_HINT}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _parse_args():
    p = argparse.ArgumentParser(
        description="Apply clean_room_mode + initial_value to the Instances.main row (merge-only)."
    )
    p.add_argument("--instance", default=DEFAULT_INSTANCE_ID, help=f"Instances row id (default: {DEFAULT_INSTANCE_ID})")
    p.add_argument("--initial-value", type=float, default=None,
                   help="Initial value in USD (REQUIRED with --apply; the instance's starting cash).")
    p.add_argument("--rollback", action="store_true",
                   help="Set clean_room_mode=False instead of applying. Leaves initial_value field alone.")
    p.add_argument("--apply", action="store_true",
                   help="Actually write the change (default: dry-run prints what would happen).")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(main(
        instance_id=args.instance,
        apply=args.apply,
        initial_value=args.initial_value,
        rollback=args.rollback,
    ))
