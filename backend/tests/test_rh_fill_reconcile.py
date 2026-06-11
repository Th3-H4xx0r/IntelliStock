"""Hardening (2026-06-10): reconciliation-driven fill-alert catch-up.

When the 60s polling loop detects a terminal fill it triggers a forced
position refresh; right after, we scan RH's recent FILLED orders and page any
that were never alerted (the sibling fills the loop missed — the "8 buys, 1
notif" incident). Already-alerted orders and fills outside the recent window
are skipped, so a process restart can't replay stale fills.
"""
from __future__ import annotations

import types
from collections import OrderedDict
from datetime import datetime, timedelta, timezone


def _now_iso(delta_sec: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_sec)).isoformat()


def _make_adapter(filled_orders, process_start=None):
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._instance_id = "test-instance"
    a._alerted_fills = OrderedDict()
    a._ALERTED_FILLS_CAP = 2048
    a._reconcile_window_sec = 1800
    # default: started long ago so recent fills pass the process-start floor
    a._process_start_utc = process_start or (
        datetime.now(timezone.utc) - timedelta(hours=1))
    a._pending_reconcile = False
    a._client = types.SimpleNamespace(
        list_orders=lambda **kw: list(filled_orders),
    )
    # stub the cid resolver so we don't touch the WAL
    a._cid_for_broker_order_id = lambda oid: f"cid-{oid}"
    return a


def test_reconcile_pages_only_unalerted_recent_fills():
    orders = [
        {"id": "oid1", "symbol": "AAPL", "side": "buy",
         "cumulative_quantity": "1", "average_price": "100", "updated_at": _now_iso(-5)},
        {"id": "oid2", "symbol": "MSFT", "side": "buy",
         "cumulative_quantity": "2", "average_price": "200", "updated_at": _now_iso(-5)},
    ]
    a = _make_adapter(orders)
    a._alerted_fills["oid1"] = 1.0  # already paged by the polling path
    calls = []
    a._alert_fill = lambda **kw: calls.append(kw)

    a._reconcile_fill_alerts()

    assert len(calls) == 1
    assert calls[0]["symbol"] == "MSFT"
    assert calls[0]["broker_order_id"] == "oid2"
    assert "oid2" in a._alerted_fills


def test_reconcile_skips_fills_outside_window():
    orders = [
        {"id": "oidOld", "symbol": "TSLA", "side": "buy",
         "cumulative_quantity": "1", "average_price": "300",
         "updated_at": _now_iso(-3 * 3600)},  # 3h ago — outside 30-min window
    ]
    a = _make_adapter(orders)
    calls = []
    a._alert_fill = lambda **kw: calls.append(kw)

    a._reconcile_fill_alerts()

    assert calls == []
    assert "oidOld" not in a._alerted_fills


def test_reconcile_skips_fills_before_process_start():
    """Restart-flood guard: a fill that is recent (within the window) but
    predates THIS process must not be re-paged (the empty post-restart ledger
    would otherwise replay it)."""
    orders = [
        {"id": "oidPre", "symbol": "NVDA", "side": "buy",
         "cumulative_quantity": "1", "average_price": "500",
         "updated_at": _now_iso(-60)},  # 60s ago — within 30min window…
    ]
    # …but the process started 10s ago, so the 60s-old fill predates it.
    a = _make_adapter(orders, process_start=datetime.now(timezone.utc) - timedelta(seconds=10))
    calls = []
    a._alert_fill = lambda **kw: calls.append(kw)

    a._reconcile_fill_alerts()

    assert calls == []
    assert "oidPre" not in a._alerted_fills


def test_reconcile_skips_missing_timestamp():
    """An order with no usable timestamp can't be confirmed fresh -> not paged
    (prevents stale timestamp-less fills from flooding after a restart)."""
    orders = [
        {"id": "oidNoTs", "symbol": "AMD", "side": "buy",
         "cumulative_quantity": "1", "average_price": "100"},  # no updated_at
    ]
    a = _make_adapter(orders)
    calls = []
    a._alert_fill = lambda **kw: calls.append(kw)

    a._reconcile_fill_alerts()

    assert calls == []


def test_reconcile_swallows_client_errors():
    a = _make_adapter([])
    def _boom(**kw):
        raise RuntimeError("RH down")
    a._client.list_orders = _boom
    a._alert_fill = lambda **kw: None
    # must not raise
    a._reconcile_fill_alerts()
