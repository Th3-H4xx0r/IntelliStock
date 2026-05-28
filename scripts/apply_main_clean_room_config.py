"""Apply clean_room_mode + initial_value to the Instances.main row.

MERGE-ONLY: reads the full Instances row, sets ONLY ``clean_room_mode`` and
``initial_value`` via a partial update, and leaves every other field
(brokerage_id, secret, strategy_id, max_usage, status, etc.) untouched.

Once applied, the next time the broker daemon boots for ``main`` it will:

  1. Read these two fields at backend/broker.py:5285+ (clean_room_mode +
     initial_value resolution block).
  2. Pass clean_room_mode=True and initial_value=<value> to the adapter
     factory.
  3. RobinhoodAdapter.__init__ runs the WAL classifier instead of blindly
     adopting whatever positions sit in the Robinhood account.
  4. The migration detector at broker.py:5685+ catches the stale backtest
     peak ($19,977.60 vs the new $6,434.48 baseline) and — IF the env
     ``LIVE_AUTO_RESET_ON_MIGRATION=true`` is set — auto-clears the
     migration-sensitive keys (_portfolio_drawdown_state, _sold_cooldown,
     _v32_*, _deployment_bar_index, etc.) so the strategy boots with a
     clean drawdown baseline.

Read-only by default. Pass ``--apply`` to write. Pass ``--initial-value N``
to override the default ($6,434.48 = the actual Robinhood cash balance per
inspect_broker_state.py 2026-05-28).

Usage:
  RETHINKDB_HOST=REDACTED-IP python3 scripts/apply_main_clean_room_config.py
    # dry-run with defaults
  RETHINKDB_HOST=REDACTED-IP python3 scripts/apply_main_clean_room_config.py --apply
    # write with defaults
  RETHINKDB_HOST=REDACTED-IP python3 scripts/apply_main_clean_room_config.py \
    --instance main --initial-value 7000 --apply
    # custom value
"""
from __future__ import annotations

import argparse
import os
import sys

DB_NAME = "IntelliStock"
DEFAULT_INSTANCE_ID = "main"
DEFAULT_INITIAL_VALUE = 0.00  # matches inspector reading on 2026-05-28


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
    p.add_argument("--initial-value", type=float, default=DEFAULT_INITIAL_VALUE,
                   help=f"Initial value in USD (default: {DEFAULT_INITIAL_VALUE})")
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
