"""Postgres-backed runtime state for live mode.

This module exposes NEW tables ONLY. It MUST NOT read, write, or even reference
the Strategies table. An explicit _FORBIDDEN set is checked at every write path
as a runtime guard; the bug-checking agents verify the source grep too.

Tables introduced:
- LiveOrderWAL       - write-ahead log for every live order (broker_adapters/_wal.py)
- LiveOrderLifecycle - append-only normalized order-event histories
- NexusRuntimeState  - per-instance+scope runtime cache (blacklist, cooldowns, peaks)
- LiveDecisionAudit  - append-only audit of every live decision
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from db import store
from db import schema


DB_NAME = "IntelliStock"
WAL_TABLE = "LiveOrderWAL"
LIFECYCLE_TABLE = "LiveOrderLifecycle"
STATE_TABLE = "NexusRuntimeState"
AUDIT_TABLE = "LiveDecisionAudit"

_FORBIDDEN_TABLES = frozenset({"Strategies"})


def _assert_table_allowed(name: str) -> None:
    if name in _FORBIDDEN_TABLES:
        raise RuntimeError(
            f"nexus_runtime_state refuses to touch table {name!r}. "
            "Strategies config is frozen; use live_mode_overrides.py for runtime adjustments."
        )


class _StoreConn:
    """Placeholder handed to the ``with _conn() as c`` blocks that survive.

    The store takes its own pooled connection per operation, so nothing is
    opened here. ``live_risk_state.RethinkRiskBackend`` still imports ``_conn``
    as its default connection factory, so the context manager keeps existing.
    """

    def close(self) -> None:
        return None


@contextmanager
def _conn() -> Iterator:
    yield _StoreConn()


def ensure_tables() -> None:
    """Create WAL/state/audit tables if missing. Raises if a forbidden table is requested."""
    for t in (WAL_TABLE, LIFECYCLE_TABLE, STATE_TABLE, AUDIT_TABLE):
        _assert_table_allowed(t)
    for t in (WAL_TABLE, LIFECYCLE_TABLE, STATE_TABLE, AUDIT_TABLE):
        schema.ensure_table(t)


def ensure_alpha_wal_indexes() -> None:
    """The LiveOrderWAL secondary indexes the benchmark-alpha system queries
    by (Task 5): ``instance_id`` for instance-scoped reconciliation and
    ``client_order_id`` for prefix scans that replace the full-table filter in
    ``list_filled_for_prefix``. Both are declared in ``db/schema.py``, so this
    is now the same idempotent ensure_table the rest of the module calls."""
    _assert_table_allowed(WAL_TABLE)
    schema.ensure_table(WAL_TABLE)


# ---- WAL store adapter ----

class WALStore:
    """Storage backend for LiveOrderWAL, wrapping the LiveOrderWAL table."""

    def __init__(self) -> None:
        _assert_table_allowed(WAL_TABLE)

    def insert(self, row: dict) -> None:
        _assert_table_allowed(WAL_TABLE)
        payload = dict(row, id=row["client_order_id"])
        store.insert(WAL_TABLE, payload, conflict="error")

    def update(self, cid: str, patch: dict) -> None:
        _assert_table_allowed(WAL_TABLE)
        store.update(WAL_TABLE, cid, patch)

    def get(self, cid: str) -> Optional[dict]:
        _assert_table_allowed(WAL_TABLE)
        row = store.get(WAL_TABLE, cid)
        if not row:
            return None
        row.pop("id", None)
        return row

    def list_open(self) -> list[dict]:
        _assert_table_allowed(WAL_TABLE)
        terminal = {"filled", "canceled", "rejected", "expired"}
        rows = store.run(WAL_TABLE)
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

        The scan stays a full-table read in Python, exactly as it was under
        RethinkDB: pushing the prefix into SQL would change which rows a row
        missing ``client_order_id`` falls into, and every other filter here
        (``filled_qty`` truthiness, the ``dry-`` exclusion) is Python
        truthiness that has no faithful SQL twin.
        """
        _assert_table_allowed(WAL_TABLE)
        rows = store.run(WAL_TABLE)
        out: list[dict] = []
        for row in rows:
            cid = row.get("client_order_id") or ""
            if not cid.startswith(cid_prefix):
                continue
            if not row.get("filled_qty"):
                continue
            # Exclude historical synthetic fills (broker_order_id 'dry-*').
            # They were never sent to a broker, so
            # the clean-room classifier (the only caller of this query) must not
            # reconstruct them as real strategy positions/trades.
            if str(row.get("broker_order_id") or "").startswith("dry-"):
                continue
            if since_utc is not None:
                ts = row.get("updated_at_utc") or row.get("created_at_utc")
                if ts is not None and ts < since_utc:
                    continue
            row.pop("id", None)
            out.append(row)
        return out


class PostgresLifecycleBackend:
    """Atomic backend for ``live_orders.OrderLifecycleStore``.

    Each row owns one immutable client-order identity. ``events`` only grows;
    ``version`` is updated by an atomic compare-and-set expression.
    """

    def __init__(self) -> None:
        _assert_table_allowed(LIFECYCLE_TABLE)

    def create(self, row) -> bool:
        _assert_table_allowed(LIFECYCLE_TABLE)
        payload = dict(row)
        payload["id"] = payload["client_order_id"]
        try:
            res = store.insert(LIFECYCLE_TABLE, payload, conflict="error")
            if res["errors"]:
                raise RuntimeError(res["first_error"] or "insert failed")
            return True
        except Exception:
            # A duplicate deterministic identity is idempotent. Any storage
            # outage still propagates because the confirming read also fails.
            if self.get(payload["client_order_id"]) is not None:
                return False
            raise

    def get(self, client_order_id: str) -> Optional[dict]:
        _assert_table_allowed(LIFECYCLE_TABLE)
        row = store.get(LIFECYCLE_TABLE, str(client_order_id))
        if row is None:
            return None
        row.pop("id", None)
        return row

    def compare_and_swap(
        self, client_order_id: str, expected_version: int, row
    ) -> bool:
        """One statement: UPDATE ... WHERE id = %s AND <version matches>.

        The ReQL form was ``.update(lambda cur: r.branch(cur['version']
        .default(-1).eq(expected), patch, {}))`` -- a DEEP MERGE of ``patch``
        on match, not a replace, so a key present on the stored row and absent
        from ``patch`` (``id``, which is popped below) survives. ``store.update``
        over a Selection is the same deep merge in one server-side statement,
        and it counts a no-change patch as ``unchanged`` exactly as ReQL did.
        """
        _assert_table_allowed(LIFECYCLE_TABLE)
        patch = dict(row)
        patch.pop("id", None)
        rid = store.coerce_id(LIFECYCLE_TABLE, str(client_order_id))
        # The dict predicate carries the guarded ::numeric compare, so a
        # version stored as 3.0 still matches an expected 3, as it did in ReQL.
        sel = store.filter(
            LIFECYCLE_TABLE, {"version": int(expected_version)}
        ).where("id = %s", (rid,))
        result = store.update(LIFECYCLE_TABLE, sel, patch)
        return int(result.get("replaced", 0) or 0) == 1

    def list_for_instance(self, instance_id: str) -> list[dict]:
        _assert_table_allowed(LIFECYCLE_TABLE)
        rows = store.run(
            store.filter(LIFECYCLE_TABLE, {"instance_id": str(instance_id)}))
        for row in rows:
            row.pop("id", None)
        return rows


# The class was named for the driver, not for what it does. Both names refer
# to the same object so broker.py (a different port group) keeps importing.
RethinkLifecycleBackend = PostgresLifecycleBackend


# ---- NexusRuntimeState accessors ----

def load_runtime_state(instance_id: str, scope_id: str) -> dict:
    _assert_table_allowed(STATE_TABLE)
    key = f"{instance_id}:{scope_id}"
    row = store.get(STATE_TABLE, key)
    if not row:
        return {}
    row.pop("id", None)
    return row


def save_runtime_state(instance_id: str, scope_id: str, state: dict) -> None:
    _assert_table_allowed(STATE_TABLE)
    key = f"{instance_id}:{scope_id}"
    payload = dict(state, id=key)
    store.insert(STATE_TABLE, payload, conflict="replace")


# ---- LiveDecisionAudit (append-only) ----

def audit_decision(record: dict) -> None:
    """Append a live decision to the audit log. Never updates or deletes."""
    _assert_table_allowed(AUDIT_TABLE)
    store.insert(AUDIT_TABLE, dict(record))
