"""Auto-setup helpers for the live broker daemon's first clean-room boot.

The broker daemon (``backend/broker.py``) calls these AT BOOT, BEFORE the
strategy_cache snapshot hydrate, when ``Instances.<id>.clean_room_mode`` is
True. They replace the manual pre-launch steps the operator used to run by
hand:

  ``is_first_clean_room_boot(r, conn, instance_id)``
      Returns True iff the LiveBootAudit table either does not exist OR has
      zero rows for ``instance_id``. Used to detect "this is the first
      clean-room boot for this instance" so we only run the destructive
      cleanup once.

  ``run_first_clean_room_boot_cleanup(r, conn, instance_id)``
      Performs the same per-instance state wipe that
      ``scripts/clear_main_instance_lookback_state.py --instance <id> --apply``
      does — drops every per-instance operational state row (cooldowns,
      discovered stocks, market trends, active events, outcomes, etc.) while
      preserving the ``origin="backtest"`` NexusStrategyCache snapshots that
      carry the strategy's learned state. Idempotent.

  ``should_auto_reset_on_migration(env_value, clean_room_mode)``
      Pure helper that codifies the policy: explicit env truthy => yes;
      explicit env falsy => no (operator override always wins); env unset
      => yes when clean_room_mode is True, else no.

Each is a small pure-ish function so broker.py boot stays readable and the
unit tests in ``backend/tests/test_live_boot_setup.py`` exercise the policy
without booting the broker.
"""
from __future__ import annotations

from typing import Any, Optional


_DB_NAME = "IntelliStock"
_AUDIT_TABLE = "LiveBootAudit"


def is_first_clean_room_boot(r: Any, conn: Any, instance_id: str) -> bool:
    """Return True iff no prior boot audit row exists for this instance.

    Treats "LiveBootAudit table absent" as "first boot" — the very first
    clean-room launch happens before the audit table has ever been created.

    Best-effort: any RethinkDB error returns False (default to NOT re-running
    cleanup) so a transient DB hiccup doesn't trigger an unnecessary wipe.
    """
    if not instance_id:
        return False
    try:
        tables = set(r.db(_DB_NAME).table_list().run(conn))
        if _AUDIT_TABLE not in tables:
            return True
        count = int(
            r.db(_DB_NAME).table(_AUDIT_TABLE)
            .filter(lambda row: row["instance_id"] == str(instance_id))
            .count()
            .run(conn)
            or 0
        )
        return count == 0
    except Exception:
        # Fail-safe: if we cannot tell, do NOT re-run cleanup.
        return False


def run_first_clean_room_boot_cleanup(
    r: Any,
    conn: Any,
    instance_id: str,
) -> dict[str, Any]:
    """Run the per-instance full-scope clear in-process. Returns the same
    shape ``clear_instance_state.execute`` returns:

        {
          "instance_id": "main",
          "scope": "full_instance",
          "apply": True,
          "tables": [ {table, would_delete, deleted, skipped, reason}, ... ],
          "total_would_delete": int,
          "total_deleted": int,
        }

    Preserves ``origin="backtest"`` NexusStrategyCache snapshots — same
    filter the CLI script uses, identical safety semantics.

    NEVER raises on RethinkDB failure — returns a dict with ``error`` set
    so the broker daemon can log and continue (the cleanup is a hygiene
    step, not a safety-critical operation; the migration detector + clean-
    room WAL classifier still cover the actual safety surface).
    """
    try:
        from clear_instance_state import execute as _clear_execute
    except ImportError as e:
        return {
            "instance_id": instance_id,
            "scope": "full_instance",
            "apply": True,
            "tables": [],
            "total_would_delete": 0,
            "total_deleted": 0,
            "error": f"clear_instance_state import failed: {e!r}",
        }

    try:
        return _clear_execute(
            conn,
            instance_id=str(instance_id),
            scope="full_instance",
            apply=True,
        )
    except Exception as e:
        return {
            "instance_id": instance_id,
            "scope": "full_instance",
            "apply": True,
            "tables": [],
            "total_would_delete": 0,
            "total_deleted": 0,
            "error": f"clear execute failed: {type(e).__name__}: {e!r}",
        }


def should_auto_reset_on_migration(
    env_value: Optional[str],
    clean_room_mode: bool,
) -> bool:
    """Decide whether the account-migration detector should auto-reset.

    Policy:
      - explicit env truthy   -> True
      - explicit env falsy    -> False  (operator override always wins)
      - env unset/blank       -> mirror clean_room_mode

    Truthy strings: {"1","true","yes","on"}; falsy: {"0","false","no","off"}.
    Any other value (e.g. ``"maybe"``) treated as unset.
    """
    raw = (env_value or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return bool(clean_room_mode)
