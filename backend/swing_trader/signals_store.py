"""SwingSignals and SwingWheelScans (spec §7, contract §5-6).

ST kept reviews in pending_trades.json / pending_wheel.json and scans in
wheel_trades.csv; here they are rows. `store` is a module attribute so tests
swap in the FakeStore fixture. Signal ids are DETERMINISTIC per
(instance, lane, session, symbol): a crashed and resumed scan finds its own
row instead of re-scoring the candidate and notifying twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from db import store  # noqa: F401  (tests monkeypatch this name)
from db.store import P

from swing_trader.refdata import SCANS_TABLE, SIGNALS_TABLE, SWING_TABLES

_NAMESPACE = uuid.UUID("5f0c3d1e-9a4b-4c1e-8f7a-2b6d9e0a1c35")
OPEN_STATUSES = ("auto_approved", "submitted")
_ENSURED = False


def ensure_tables() -> None:
    """Create the six tables once per process. DDL stays in db/schema.py; a
    test's FakeStore needs none."""
    global _ENSURED
    if _ENSURED:
        return
    from db import schema
    from db import store as real_store
    if store is real_store:
        schema.ensure_schema(tables=list(SWING_TABLES))
    _ENSURED = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def signal_id_for(instance_id, lane, session, symbol) -> str:
    key = f"{instance_id}|{lane}|{str(session)[:10]}|{str(symbol).strip().upper()}"
    return uuid.uuid5(_NAMESPACE, key).hex


def new_signal(*, instance_id, lane, symbol, session, score, recommendation, reasoning,
               key_risks, size_adjustment, proposal, status, context=None,
               created_at=None) -> dict:
    return {
        "id": signal_id_for(instance_id, lane, session, symbol),
        "instance_id": str(instance_id), "lane": lane,
        "symbol": str(symbol).strip().upper(), "session": str(session)[:10],
        "created_at": created_at or _now_iso(), "score": score,
        "recommendation": str(recommendation or "").upper(),
        "reasoning": str(reasoning or ""),
        "key_risks": [str(r) for r in (key_risks or [])],
        "size_adjustment": size_adjustment, "proposal": dict(proposal or {}),
        "status": status, "decided_by": None, "decided_at": None,
        "decision_reason": None, "order_client_id": None, "outcome": None,
        "context": dict(context or {}),
    }


def insert_signal(doc: dict) -> str:
    """Insert once; a row that already exists (a resumed scan) is kept."""
    ensure_tables()
    store.insert(SIGNALS_TABLE, doc, conflict="error")
    return doc["id"]


def get_signal(signal_id):
    if not signal_id:
        return None
    return store.get(SIGNALS_TABLE, str(signal_id))


def update_signal(signal_id, patch: dict) -> None:
    store.update(SIGNALS_TABLE, str(signal_id), dict(patch))


def cas_signal(signal_id, *, expect_status, doc: dict) -> bool:
    """Replace the row only while its status is still `expect_status`."""
    return store.replace_if(SIGNALS_TABLE, str(signal_id),
                            when=P.field("status").eq(str(expect_status)),
                            doc=dict(doc)) is not None


def list_signals(instance_id, status=None, limit: int = 100) -> list:
    pred = P.field("instance_id").eq(str(instance_id))
    if status:
        pred = pred & P.field("status").eq(str(status))
    sel = store.order_by(store.filter(SIGNALS_TABLE, pred),
                         fields=(store.desc("created_at"),))
    return list(store.run(store.limit(sel, max(1, min(int(limit), 500)))))


def all_signals(instance_id) -> list:
    return list(store.iter(store.filter(
        SIGNALS_TABLE, P.field("instance_id").eq(str(instance_id)))))


def swing_owned_symbols(instance_id, held) -> set:
    """Held symbols the swing lane entered and has not closed: a swing row
    that was auto-approved or submitted and carries no outcome yet. ST read
    the same fact off paper_trades.csv (wheel_trader.py:324-345)."""
    held = {str(s).strip().upper() for s in (held or ())}
    if not held:
        return set()
    rows = store.run(store.filter(
        SIGNALS_TABLE,
        P.field("instance_id").eq(str(instance_id)) & P.field("lane").eq("swing")))
    return {str(r.get("symbol") or "").upper() for r in rows
            if str(r.get("symbol") or "").upper() in held
            and r.get("status") in OPEN_STATUSES and not r.get("outcome")}


def insert_wheel_scan(row: dict) -> str:
    """One row per candidate per session; a resumed scan replaces its row."""
    ensure_tables()
    doc = dict(row)
    doc["id"] = doc.get("id") or signal_id_for(doc.get("instance_id"), "wheelscan",
                                               doc.get("session"), doc.get("symbol"))
    doc.setdefault("created_at", _now_iso())
    store.insert(SCANS_TABLE, doc, conflict="replace")
    return doc["id"]


def list_wheel_scans(instance_id, limit: int = 50) -> list:
    sel = store.order_by(store.filter(SCANS_TABLE, P.field("instance_id").eq(str(instance_id))),
                         fields=(store.desc("created_at"),))
    return list(store.run(store.limit(sel, max(1, min(int(limit), 500)))))
