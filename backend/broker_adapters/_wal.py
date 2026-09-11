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
    # Ownership. Written by ``record_intent`` from the WAL's bound owner.
    # Rows written before ownership stamping shipped carry neither, and the
    # owner-scoped reader treats those as NOT owned (fail closed).
    instance_id: Optional[str] = None
    account_id: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveOrderWAL:
    """Write-ahead log scoped to one (instance_id, account_id) owner.

    Ownership used to be inferred from the first 8 alphanumeric characters of
    the instance id embedded in every client_order_id. Two instances whose ids
    share those 8 characters -- ``strategy-eb`` and ``strategy-eb-lab`` both
    collapse to ``strategy-`` -- read each other's fills, so a position held by
    a human in one account could be adopted as strategy-owned from the other
    instance's WAL and then sold. The owner is now stamped on the row and
    matched exactly.
    """

    def __init__(
        self,
        store: Store,
        *,
        instance_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ):
        self._store = store
        self._instance_id = str(instance_id or "").strip()
        self._account_id = str(account_id or "").strip()

    def bind_owner(self, *, instance_id: str, account_id: str) -> None:
        """Name the instance and brokerage account that own every row this
        WAL writes from here on. Callers that learn the account identity only
        after construction (the adapter factory) use this."""
        self._instance_id = str(instance_id or "").strip()
        self._account_id = str(account_id or "").strip()

    @property
    def owner(self) -> tuple[str, str]:
        return (self._instance_id, self._account_id)

    def record_intent(
        self,
        cid: str,
        symbol: str,
        side: str,
        qty: Optional[float],
        notional: Optional[float],
    ) -> None:
        ts = _now()
        row = {
            "client_order_id": cid,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "notional": notional,
            "state": "intent",
            "created_at_utc": ts,
            "updated_at_utc": ts,
        }
        # Stamp the owner so a later boot can tell this instance's fills from
        # a sibling's. Only written when known: an unbound WAL leaves the
        # fields off, and the owner-scoped reader then owns nothing.
        if self._instance_id:
            row["instance_id"] = self._instance_id
        if self._account_id:
            row["account_id"] = self._account_id
        self._store.insert(row)

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

    def list_filled_for_owner(
        self,
        instance_id: Optional[str],
        account_id: Optional[str],
        since_utc: Optional[str] = None,
    ) -> list[dict]:
        """Return this owner's filled rows: ``instance_id`` AND ``account_id``
        must match exactly.

        Fails closed. A blank instance_id or account_id owns nothing rather
        than degrading into "every row", and rows that carry neither field
        (written before ownership stamping shipped) are never returned -- an
        un-provenanced position is treated as human-held, which means the
        strategy quarantines it instead of selling it.
        """
        inst = str(instance_id or "").strip()
        acct = str(account_id or "").strip()
        if not inst or not acct:
            return []
        fn = getattr(self._store, "list_filled_for_owner", None)
        if fn is None:
            return []
        return fn(inst, acct, since_utc=since_utc)

    def list_filled_for_prefix(
        self,
        cid_prefix: str,
        since_utc: Optional[str] = None,
    ) -> list[dict]:
        """Return WAL rows whose client_order_id starts with ``cid_prefix``
        and have a non-zero ``filled_qty``.

        When this WAL knows its owner, the prefix is NOT the ownership signal:
        the read is scoped to (instance_id, account_id) first and the prefix
        is then applied on top. An unbound WAL keeps the legacy prefix-only
        behaviour so existing non-live callers and test doubles are unchanged.

        Used by the broker-state classifier at adapter boot under
        ``clean_room_mode=True``.
        """
        if self._instance_id and self._account_id:
            rows = self.list_filled_for_owner(
                self._instance_id, self._account_id, since_utc=since_utc
            )
            return [
                r for r in rows
                if str(r.get("client_order_id") or "").startswith(cid_prefix)
            ]
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

    def list_filled_for_owner(
        self,
        instance_id: str,
        account_id: str,
        since_utc: Optional[str] = None,
    ) -> list[dict]:
        """Mirror of WALStore.list_filled_for_owner for tests."""
        out: list[dict] = []
        for r in self._rows.values():
            if str(r.get("instance_id") or "") != str(instance_id or ""):
                continue
            if str(r.get("account_id") or "") != str(account_id or ""):
                continue
            if not r.get("filled_qty"):
                continue
            if str(r.get("broker_order_id") or "").startswith("dry-"):
                continue
            if since_utc is not None:
                ts = r.get("updated_at_utc") or r.get("created_at_utc")
                if ts is not None and ts < since_utc:
                    continue
            out.append(dict(r))
        return out

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
