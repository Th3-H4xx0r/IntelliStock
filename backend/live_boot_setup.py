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
    """Return True iff no prior CLEAN-ROOM boot audit row exists for this instance.

    Treats "LiveBootAudit table absent" as "first boot" — the very first
    clean-room launch happens before the audit table has ever been created.

    0-B (bug-sweep 2026-05-28): count ONLY rows with ``mode == "clean_room"``.
    The per-boot audit row is written on EVERY boot, including legacy/smoke boots
    (``mode="legacy"``, broker.py:5461). The old "any row" check meant a single
    prior legacy boot would suppress the first clean-room cleanup, leaving stale
    per-instance state on a real-money launch. A crash-before-the-audit-write just
    re-runs the cleanup next boot, which is harmless (the wipe is idempotent and
    preserves the origin=backtest snapshot).

    Best-effort: any RethinkDB error returns False (default to NOT re-running
    cleanup) so a transient DB hiccup doesn't trigger an unnecessary wipe.
    """
    if not instance_id:
        return False
    try:
        tables = set(r.db(_DB_NAME).table_list().run(conn))
        if _AUDIT_TABLE not in tables:
            return True
        rows = list(
            r.db(_DB_NAME).table(_AUDIT_TABLE)
            .filter(lambda row: row["instance_id"] == str(instance_id))
            .run(conn)
        )
        clean_room_rows = [x for x in rows if (x or {}).get("mode") == "clean_room"]
        return len(clean_room_rows) == 0
    except Exception:
        # Fail-safe: if we cannot tell, do NOT re-run cleanup.
        return False


def rebaseline_clean_room_drawdown(cache: dict, live_equity: float) -> Optional[dict]:
    """Re-baseline a backtest-origin drawdown state to the live account (2-B).

    In clean_room_mode the only NexusStrategyCache row the cleanup preserves is
    the ``origin="backtest"`` snapshot, so any ``_portfolio_drawdown_state`` that
    the boot hydrate merged carries the BACKTEST peak / halt flag. The account-
    migration detector only re-baselines when the cached peak exceeds live equity
    by >1.40x AND there are 0 positions; a matched-equity or modest-peak backtest
    leaves a stale peak that silently demotes (or halts) day-1 buys.

    This resets the drawdown state to a clean live baseline (peak == last ==
    live_equity, halt cleared) whenever a drawdown state is present. Returns the
    new state, or None if there was nothing to reset (so the caller can log).
    """
    dd = cache.get("_portfolio_drawdown_state")
    if not isinstance(dd, dict) or not dd:
        return None
    try:
        eq = float(live_equity)
    except (TypeError, ValueError):
        return None
    new_state = {
        "peak_value": eq,
        "last_value": eq,
        "halt_active": False,
        "up_days": 0,
    }
    cache["_portfolio_drawdown_state"] = new_state
    return new_state


def strip_stale_momentum_baseline(cache: dict) -> int:
    """Drop backtest-era ``first_seen_price`` from hydrated momentum-watchlist
    entries (2-E).

    ``_momentum_watchlist`` rides the origin="backtest" snapshot into live. Each
    entry's ``first_seen_price`` was captured the bar the ticker first entered the
    watchlist during the backtest — possibly 120+ days stale — and is used as a
    runup ceiling in the buy gate. Stripping it on hydrate lets the baseline
    re-establish from live bars. Returns the number of entries cleaned.
    """
    wl = cache.get("_momentum_watchlist")
    if not isinstance(wl, dict):
        return 0
    n = 0
    for entry in wl.values():
        if isinstance(entry, dict) and "first_seen_price" in entry:
            entry.pop("first_seen_price", None)
            n += 1
    return n


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
