"""RethinkDB-backed runtime state for live mode.

This module exposes NEW tables ONLY. It MUST NOT read, write, or even reference
the Strategies table. An explicit _FORBIDDEN set is checked at every write path
as a runtime guard; the bug-checking agents verify the source grep too.

Tables introduced:
- LiveOrderWAL       - write-ahead log for every live order (broker_adapters/_wal.py)
- NexusRuntimeState  - per-instance+scope runtime cache (blacklist, cooldowns, peaks)
- LiveDecisionAudit  - append-only audit of every live decision
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

try:
    from rethinkdb import RethinkDB
    _r = RethinkDB()
    _HAS_RDB = True
except ImportError:
    _r = None
    _HAS_RDB = False


DB_NAME = "IntelliStock"
WAL_TABLE = "LiveOrderWAL"
STATE_TABLE = "NexusRuntimeState"
AUDIT_TABLE = "LiveDecisionAudit"

_FORBIDDEN_TABLES = frozenset({"Strategies"})


def _host() -> str:
    return os.environ.get("RETHINKDB_HOST", "localhost")


def _port() -> int:
    return int(os.environ.get("RETHINKDB_PORT", "28015"))


def _assert_table_allowed(name: str) -> None:
    if name in _FORBIDDEN_TABLES:
        raise RuntimeError(
            f"nexus_runtime_state refuses to touch table {name!r}. "
            "Strategies config is frozen; use live_mode_overrides.py for runtime adjustments."
        )


@contextmanager
def _conn() -> Iterator:
    if not _HAS_RDB or _r is None:
        raise RuntimeError("rethinkdb not available")
    c = _r.connect(host=_host(), port=_port(), timeout=10)
    try:
        yield c
    finally:
        try:
            c.close()
        except Exception:
            pass


def ensure_tables() -> None:
    """Create WAL/state/audit tables if missing. Raises if a forbidden table is requested."""
    for t in (WAL_TABLE, STATE_TABLE, AUDIT_TABLE):
        _assert_table_allowed(t)
    with _conn() as c:
        existing = set(_r.db(DB_NAME).table_list().run(c))
        for t in (WAL_TABLE, STATE_TABLE, AUDIT_TABLE):
            if t not in existing:
                _r.db(DB_NAME).table_create(t).run(c)


# ---- WAL store adapter ----

class WALStore:
    """Storage backend for LiveOrderWAL, wrapping RethinkDB LiveOrderWAL table."""

    def __init__(self) -> None:
        _assert_table_allowed(WAL_TABLE)

    def insert(self, row: dict) -> None:
        _assert_table_allowed(WAL_TABLE)
        payload = dict(row, id=row["client_order_id"])
        with _conn() as c:
            _r.db(DB_NAME).table(WAL_TABLE).insert(payload, conflict="error").run(c)

    def update(self, cid: str, patch: dict) -> None:
        _assert_table_allowed(WAL_TABLE)
        with _conn() as c:
            _r.db(DB_NAME).table(WAL_TABLE).get(cid).update(patch).run(c)

    def get(self, cid: str) -> Optional[dict]:
        _assert_table_allowed(WAL_TABLE)
        with _conn() as c:
            row = _r.db(DB_NAME).table(WAL_TABLE).get(cid).run(c)
        if not row:
            return None
        row.pop("id", None)
        return row

    def list_open(self) -> list[dict]:
        _assert_table_allowed(WAL_TABLE)
        terminal = {"filled", "canceled", "rejected", "expired"}
        with _conn() as c:
            rows = list(_r.db(DB_NAME).table(WAL_TABLE).run(c))
        return [r for r in rows if r.get("state") not in terminal]

    def list_filled_for_prefix(
        self,
        cid_prefix: str,
        since_utc: Optional[str] = None,
    ) -> list[dict]:
        """Return WAL rows whose client_order_id starts with ``cid_prefix``
        and have a non-zero ``filled_qty``, optionally filtered by
        ``updated_at_utc >= since_utc``.

        Used by the broker-state classifier (broker_adapters/_classifier.py)
        to determine which broker positions are strategy-owned at boot.

        WAL is globally-scoped (no instance_id field); the cid prefix is the
        ONLY signal that distinguishes one live instance's fills from
        another's. For instance_id="main", the prefix is "main-" — see
        broker_adapters/_client_order_id.py for the exact format.

        NOTE: this scans the WAL table without a secondary index. Fine for
        the few-thousand-row WAL typical of a single live instance over six
        months. If we deploy many concurrent instances we should add an
        index on client_order_id.
        """
        _assert_table_allowed(WAL_TABLE)
        with _conn() as c:
            rows = list(_r.db(DB_NAME).table(WAL_TABLE).run(c))
        out: list[dict] = []
        for row in rows:
            cid = row.get("client_order_id") or ""
            if not cid.startswith(cid_prefix):
                continue
            if not row.get("filled_qty"):
                continue
            if since_utc is not None:
                ts = row.get("updated_at_utc") or row.get("created_at_utc")
                if ts is not None and ts < since_utc:
                    continue
            row.pop("id", None)
            out.append(row)
        return out


# ---- NexusRuntimeState accessors ----

def load_runtime_state(instance_id: str, scope_id: str) -> dict:
    _assert_table_allowed(STATE_TABLE)
    key = f"{instance_id}:{scope_id}"
    with _conn() as c:
        row = _r.db(DB_NAME).table(STATE_TABLE).get(key).run(c)
    if not row:
        return {}
    row.pop("id", None)
    return row


def save_runtime_state(instance_id: str, scope_id: str, state: dict) -> None:
    _assert_table_allowed(STATE_TABLE)
    key = f"{instance_id}:{scope_id}"
    payload = dict(state, id=key)
    with _conn() as c:
        _r.db(DB_NAME).table(STATE_TABLE).insert(payload, conflict="replace").run(c)


# ---- LiveDecisionAudit (append-only) ----

def audit_decision(record: dict) -> None:
    """Append a live decision to the audit log. Never updates or deletes."""
    _assert_table_allowed(AUDIT_TABLE)
    with _conn() as c:
        _r.db(DB_NAME).table(AUDIT_TABLE).insert(dict(record)).run(c)
