"""Unit tests for the WAL-based broker-position classifier and the
list_filled_for_prefix query method.

Schema we consume (per backend/broker_adapters/_wal.py + broker.py production
WAL store at backend/nexus_runtime_state.py:WALStore):
  - client_order_id (primary key, also used as the row id)
  - symbol
  - side ('BUY' / 'SELL')
  - state ('intent' | 'submitted' | 'accepted' | 'partial' | 'filled' | ...)
  - filled_qty
  - filled_avg_price
  - updated_at_utc (ISO string)

CID prefix for instance "main" is the first 8 alphanumeric chars of the
instance id + '-', so 'main-' per backend/broker_adapters/_client_order_id.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class _FakeWAL:
    """In-process WAL with the same query surface as LiveOrderWAL.list_filled_for_prefix.

    Tests give this a list of WAL row dicts; the classifier calls
    list_filled_for_prefix(prefix, since_utc) and we filter accordingly.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def list_filled_for_prefix(
        self,
        cid_prefix: str,
        since_utc: str | None = None,
    ) -> list[dict]:
        out: list[dict] = []
        for r in self._rows:
            if not (r.get("client_order_id") or "").startswith(cid_prefix):
                continue
            if not r.get("filled_qty"):
                continue
            if since_utc is not None:
                ts = r.get("updated_at_utc") or r.get("created_at_utc")
                if ts is not None and ts < since_utc:
                    continue
            out.append(r)
        return out


def test_fake_wal_filters_by_prefix_and_time():
    """Sanity test for the test stub itself (so subsequent tests are trustworthy)."""
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    old = (now - timedelta(days=400)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    rows = [
        {"client_order_id": "main-aaa-0", "symbol": "TSLA", "side": "BUY", "state": "filled",
         "filled_qty": 10.0, "filled_avg_price": 200.0, "updated_at_utc": recent},
        {"client_order_id": "other-xyz-0", "symbol": "AAPL", "side": "BUY", "state": "filled",
         "filled_qty": 5.0, "filled_avg_price": 180.0, "updated_at_utc": recent},
        {"client_order_id": "main-bbb-0", "symbol": "TSLA", "side": "BUY", "state": "canceled",
         "filled_qty": 0.0, "filled_avg_price": None, "updated_at_utc": recent},
        {"client_order_id": "main-ccc-0", "symbol": "TSLA", "side": "BUY", "state": "filled",
         "filled_qty": 7.0, "filled_avg_price": 100.0, "updated_at_utc": old},
    ]
    wal = _FakeWAL(rows)
    out = wal.list_filled_for_prefix("main-", since_utc=(now - timedelta(days=180)).isoformat())
    assert len(out) == 1
    assert out[0]["client_order_id"] == "main-aaa-0"


def test_walstore_has_list_filled_for_prefix():
    """Production WALStore in nexus_runtime_state.py must expose the query method."""
    import inspect
    from nexus_runtime_state import WALStore
    assert hasattr(WALStore, "list_filled_for_prefix"), (
        "WALStore.list_filled_for_prefix is required for clean-room classifier"
    )
    sig = inspect.signature(WALStore.list_filled_for_prefix)
    params = list(sig.parameters)
    assert "cid_prefix" in params
    assert "since_utc" in params


def test_live_order_wal_has_list_filled_for_prefix_delegate():
    """LiveOrderWAL exposes list_filled_for_prefix that delegates to the store."""
    from broker_adapters._wal import LiveOrderWAL, InMemoryStore
    store = InMemoryStore()
    wal = LiveOrderWAL(store)
    assert hasattr(wal, "list_filled_for_prefix")
    # InMemoryStore implements it; default returns empty list when no rows
    assert wal.list_filled_for_prefix("main-") == []


def test_inmemory_store_list_filled_for_prefix():
    """InMemoryStore implements list_filled_for_prefix matching WALStore's contract."""
    from broker_adapters._wal import InMemoryStore
    store = InMemoryStore()
    store.insert({
        "client_order_id": "main-aaa-0", "symbol": "TSLA", "side": "BUY",
        "state": "filled", "filled_qty": 10.0, "filled_avg_price": 200.0,
        "updated_at_utc": "2026-05-20T00:00:00+00:00",
    })
    store.insert({
        "client_order_id": "other-xyz-0", "symbol": "AAPL", "side": "BUY",
        "state": "filled", "filled_qty": 5.0, "filled_avg_price": 180.0,
        "updated_at_utc": "2026-05-20T00:00:00+00:00",
    })
    rows = store.list_filled_for_prefix("main-")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "TSLA"
