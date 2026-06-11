"""Hardening (2026-06-10): a Robinhood fill alert must fire EXACTLY ONCE per
order across both the polling path and the reconciliation catch-up path.

Root-cause class for the "8 buys, 1 Discord fill notif" incident: fills that
the 60s polling loop missed were silently reconciled into positions without
ever paging. The reconciliation catch-up (test_rh_fill_reconcile.py) re-fires
those, and this idempotency ledger guarantees the polling path + the catch-up
path don't double-page an order that both observe.
"""
from __future__ import annotations

from collections import OrderedDict


def _make_adapter():
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)  # skip heavy __init__
    a._instance_id = "test-instance"
    a._alerted_fills = OrderedDict()  # mirrors production constructor
    return a


def test_fill_alert_fires_once_per_broker_order_id():
    a = _make_adapter()
    calls = []
    a._alert_fill = lambda **kw: calls.append(kw)

    a._fire_fill_alert("AAPL", "buy", 1.0, 100.0, "cid1", "oid1")
    a._fire_fill_alert("AAPL", "buy", 1.0, 100.0, "cid1", "oid1")  # duplicate

    assert len(calls) == 1
    assert "oid1" in a._alerted_fills


def test_fill_alert_distinct_orders_each_fire():
    a = _make_adapter()
    calls = []
    a._alert_fill = lambda **kw: calls.append(kw)

    a._fire_fill_alert("AAPL", "buy", 1.0, 100.0, "cidA", "oidA")
    a._fire_fill_alert("MSFT", "buy", 2.0, 200.0, "cidB", "oidB")

    assert len(calls) == 2
    assert {"oidA", "oidB"} <= set(a._alerted_fills.keys())


def test_fill_alert_ledger_is_bounded():
    """The ledger never grows without bound (LRU eviction backstop)."""
    a = _make_adapter()
    a._alert_fill = lambda **kw: None
    for i in range(2100):
        a._fire_fill_alert("AAPL", "buy", 1.0, 100.0, f"cid{i}", f"oid{i}")
    assert len(a._alerted_fills) <= 2048
