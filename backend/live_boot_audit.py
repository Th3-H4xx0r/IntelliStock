"""LiveBootAudit table helper.

One row per live broker boot. Captures exactly what the adapter adopted
(strategy-owned) and quarantined (external) so an operator can forensically
reconstruct "what did this instance start with at this boot".

Table is created lazily on first write (no migration required). Per-instance
scoped (id pattern ``<instance_id>|<iso-timestamp>``) so the existing
``scripts/clear_main_instance_lookback_state.py`` per-instance cleanup
sweeps it correctly.

This module is intentionally small and dependency-free at import time so
it can be unit-tested without RethinkDB connectivity. The persist function
takes an injected rethinkdb instance + connection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


DB_NAME = "IntelliStock"
TABLE = "LiveBootAudit"


def build_audit_row(
    *,
    instance_id: str,
    broker_type: str,
    mode: str,                         # "clean_room" | "legacy"
    broker_cash_at_boot: float,
    broker_positions_total: int,
    strategy_owned: dict[str, float],
    external: dict[str, dict],
    initial_value: float,
    initial_value_source: str,         # "explicit"|"instance_row"|"snapshot"|"broker_equity"
    snapshot_loaded: bool,
    snapshot_keys: int,
    trades_seeded: int,
    trades_seeded_source: str,         # "wal"|"broker_history"|"none"
    notes: Optional[list[str]] = None,
    boot_at_utc: Optional[datetime] = None,
) -> dict:
    """Construct a LiveBootAudit row dict. Does not write to the DB.

    The returned dict is suitable for direct insertion via
    ``persist_audit_row``; ``id`` is set to ``<instance_id>|<iso-timestamp>``
    so per-instance cleanup scripts can sweep it.
    """
    ts = boot_at_utc or datetime.now(timezone.utc)
    return {
        "id": f"{instance_id}|{ts.isoformat()}",
        "instance_id": instance_id,
        "boot_at_utc": ts.isoformat(),
        "broker_type": broker_type,
        "mode": mode,
        "broker_cash_at_boot": float(broker_cash_at_boot),
        "broker_positions_total": int(broker_positions_total),
        "strategy_owned_count": len(strategy_owned),
        "strategy_owned_tickers": sorted(strategy_owned.keys()),
        "external_count": len(external),
        "external_tickers_qty": {k: float(v.get("qty", 0.0) or 0.0) for k, v in external.items()},
        "external_detail": external,   # full dict for forensics
        "initial_value": float(initial_value),
        "initial_value_source": initial_value_source,
        "snapshot_loaded": bool(snapshot_loaded),
        "snapshot_keys": int(snapshot_keys),
        "trades_seeded": int(trades_seeded),
        "trades_seeded_source": trades_seeded_source,
        "notes": list(notes or []),
    }


def persist_audit_row(*, r: Any, conn: Any, row: dict, db_name: str = DB_NAME) -> dict:
    """Insert the audit row into the LiveBootAudit table.

    Auto-creates the table (and useful secondary indices) on first call so
    callers do not need a migration step. Index creation is best-effort —
    if it fails, the insert still proceeds.

    Returns the rethinkdb result dict from ``insert(...).run(conn)``.
    """
    db = r.db(db_name)
    try:
        existing = list(db.table_list().run(conn))
        if TABLE not in existing:
            db.table_create(TABLE, primary_key="id").run(conn)
            try:
                db.table(TABLE).index_create("instance_id").run(conn)
                db.table(TABLE).index_create("boot_at_utc").run(conn)
                db.table(TABLE).index_wait("instance_id", "boot_at_utc").run(conn)
            except Exception:
                # Indices are an optimization, not a correctness requirement.
                pass
    except Exception:
        # Table-creation attempt failed (likely raced with another worker
        # creating the table at the same time); fall through to insert.
        pass
    return db.table(TABLE).insert(row, conflict="replace").run(conn)
