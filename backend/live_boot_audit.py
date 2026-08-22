"""LiveBootAudit table helper.

One row per live broker boot. Captures exactly what the adapter adopted
(strategy-owned) and quarantined (external) so an operator can forensically
reconstruct "what did this instance start with at this boot".

Table is created lazily on first write (no migration required). Per-instance
scoped (id pattern ``<instance_id>|<iso-timestamp>``) so the existing
``scripts/clear_main_instance_lookback_state.py`` per-instance cleanup
sweeps it correctly.

This module is intentionally small and dependency-free at import time so
it can be unit-tested without database connectivity. The persist function
keeps its injected ``r`` + ``conn`` parameters (R26); ``r`` is now an optional
store handle and ``conn`` is ignored.
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


def persist_audit_row(*, r: Any = None, conn: Any = None, row: dict,
                      db_name: str = DB_NAME) -> dict:
    """Insert the audit row into the LiveBootAudit table.

    R25: the table and its ``instance_id`` index are declared in
    db/schema.py, so ensure_table replaces the create/index/index_wait block
    and is still best-effort -- the insert proceeds either way.

    NOTE: the ReQL block also created a ``boot_at_utc`` index. That field is
    not in schema.TABLES["LiveBootAudit"].indexed_fields and nothing queries
    on it (the only read path is by ``instance_id``), so it is dropped rather
    than carried over.

    Returns the store's InsertResult, which supports ``["inserted"]`` and
    ``.get(...)`` exactly like the old driver's result dict.
    """
    store = r if r is not None else _default_store()
    try:
        from db import schema as db_schema
        db_schema.ensure_table(TABLE)
    except Exception:
        # Ensure failed (likely raced with another worker); fall through.
        pass
    return store.insert(TABLE, row, conflict="replace")


def _default_store():
    from db import store as _s
    return _s
