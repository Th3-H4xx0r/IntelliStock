"""Write-ahead log for live orders.

State machine: intent -> submitted -> accepted -> partial -> filled
                                                          -> canceled
                                                          -> rejected
                                                          -> expired

WAL is written BEFORE the broker API call. On crash-restart, the reconciler
queries the broker for each non-terminal client_order_id to decide whether
to resubmit or adopt the existing order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol


class Store(Protocol):
    def insert(self, row: dict) -> None: ...
    def update(self, cid: str, patch: dict) -> None: ...
    def get(self, cid: str) -> Optional[dict]: ...
    def list_open(self) -> list[dict]: ...


TERMINAL_STATES = frozenset({"filled", "canceled", "rejected", "expired"})


@dataclass
class WALRecord:
    client_order_id: str
    symbol: str
    side: str
    qty: Optional[float]
    notional: Optional[float]
    state: str
    broker_order_id: Optional[str] = None
    filled_qty: float = 0.0
    filled_avg_price: Optional[float] = None
    reject_reason: Optional[str] = None
    created_at_utc: Optional[str] = None
    updated_at_utc: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveOrderWAL:
    def __init__(self, store: Store):
        self._store = store

    def record_intent(
        self,
        cid: str,
        symbol: str,
        side: str,
        qty: Optional[float],
        notional: Optional[float],
    ) -> None:
        ts = _now()
        self._store.insert({
            "client_order_id": cid,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "notional": notional,
            "state": "intent",
            "created_at_utc": ts,
            "updated_at_utc": ts,
        })

    def mark_submitted(self, cid: str, broker_order_id: str) -> None:
        self._store.update(cid, {
            "state": "submitted",
            "broker_order_id": broker_order_id,
            "updated_at_utc": _now(),
        })

    def mark_accepted(self, cid: str) -> None:
        self._store.update(cid, {"state": "accepted", "updated_at_utc": _now()})

    def mark_partial(self, cid: str, filled_qty: float, filled_avg_price: Optional[float]) -> None:
        self._store.update(cid, {
            "state": "partial",
            "filled_qty": filled_qty,
            "filled_avg_price": filled_avg_price,
            "updated_at_utc": _now(),
        })

    def mark_filled(self, cid: str, filled_qty: float, filled_avg_price: Optional[float]) -> None:
        self._store.update(cid, {
            "state": "filled",
            "filled_qty": filled_qty,
            "filled_avg_price": filled_avg_price,
            "updated_at_utc": _now(),
        })

    def mark_canceled(self, cid: str) -> None:
        self._store.update(cid, {"state": "canceled", "updated_at_utc": _now()})

    def mark_rejected(self, cid: str, reason: str) -> None:
        self._store.update(cid, {
            "state": "rejected",
            "reject_reason": reason,
            "updated_at_utc": _now(),
        })

    def mark_expired(self, cid: str) -> None:
        self._store.update(cid, {"state": "expired", "updated_at_utc": _now()})

    def get(self, cid: str) -> Optional[WALRecord]:
        row = self._store.get(cid)
        if not row:
            return None
        fields = {k: row.get(k) for k in WALRecord.__dataclass_fields__}
        return WALRecord(**fields)

    def list_open(self) -> list[WALRecord]:
        out: list[WALRecord] = []
        for r in self._store.list_open():
            rec = self.get(r["client_order_id"])
            if rec is not None and rec.state not in TERMINAL_STATES:
                out.append(rec)
        return out

    def list_filled_for_prefix(
        self,
        cid_prefix: str,
        since_utc: Optional[str] = None,
    ) -> list[dict]:
        """Return WAL rows whose client_order_id starts with ``cid_prefix``
        and have a non-zero ``filled_qty``.

        Delegates to the underlying store. Returns empty list if the store
        does not implement the query method (custom test stubs without it).

        Used by the broker-state classifier at adapter boot under
        ``clean_room_mode=True``.
        """
        fn = getattr(self._store, "list_filled_for_prefix", None)
        if fn is None:
            return []
        return fn(cid_prefix, since_utc=since_utc)


class InMemoryStore:
    """Test-only store."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def insert(self, row: dict) -> None:
        self._rows[row["client_order_id"]] = dict(row)

    def update(self, cid: str, patch: dict) -> None:
        self._rows.setdefault(cid, {"client_order_id": cid}).update(patch)

    def get(self, cid: str) -> Optional[dict]:
        r = self._rows.get(cid)
        return dict(r) if r else None

    def list_open(self) -> list[dict]:
        return [
            dict(r) for r in self._rows.values()
            if r.get("state") not in TERMINAL_STATES
        ]

    def list_filled_for_prefix(
        self,
        cid_prefix: str,
        since_utc: Optional[str] = None,
    ) -> list[dict]:
        """Mirror of WALStore.list_filled_for_prefix for tests. Returns
        rows whose client_order_id starts with ``cid_prefix`` and have
        non-zero ``filled_qty``."""
        out: list[dict] = []
        for r in self._rows.values():
            cid = r.get("client_order_id") or ""
            if not cid.startswith(cid_prefix):
                continue
            if not r.get("filled_qty"):
                continue
            # Exclude synthetic dry-run fills (mirror of WALStore).
            if str(r.get("broker_order_id") or "").startswith("dry-"):
                continue
            if since_utc is not None:
                ts = r.get("updated_at_utc") or r.get("created_at_utc")
                if ts is not None and ts < since_utc:
                    continue
            out.append(dict(r))
        return out
