"""An order closed at the broker after it left the snapshot's history window.

swing-paper, 2026-11-12: six GTC bracket legs submitted 09-28..10-09 were no
longer open at Alpaca. The history walk is bounded by `submitted_at` (30 days),
so nothing returned them, their lifecycle rows stayed `acknowledged`, every
reconcile raised `local_order_missing_broker`, and the order gate blocked every
order on the account, risk exits included.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from live_orders import StartupReconciler
from live_order_task8_helpers import intent

from test_startup_reconciliation import _service


def _adapter(client):
    from broker_adapters._wal import InMemoryStore, LiveOrderWAL
    from broker_adapters.alpaca import AlpacaAdapter

    account = MagicMock(
        cash="1000", buying_power="1000", daytrading_buying_power="1000",
        equity="1000", last_equity="1000", pattern_day_trader=False,
        daytrade_count=0, account_blocked=False, trading_blocked=False,
    )
    client.get_account.return_value = account
    client.get_all_positions.return_value = []
    client.get_orders.return_value = []          # not open, not in the 30-day history
    client._session = None
    return AlpacaAdapter(
        api_key="k", api_secret="s", paper=True, instance_id="instance-1",
        wal=LiveOrderWAL(InMemoryStore()), initial_value=1000,
        seed_trades_from_broker=False, _test_client=client,
    )


def _raw(order, record, status="canceled"):
    return SimpleNamespace(
        id=str(record.broker_order_id or "broker-1"),
        client_order_id=order.idempotency_key,
        symbol=order.symbol,
        side=SimpleNamespace(value=order.side.value),
        status=SimpleNamespace(value=status),
        qty=str(order.quantity),
        filled_qty="0",
        filled_avg_price=None,
        order_class=None,
        updated_at=datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc),
        submitted_at=datetime(2026, 9, 28, 13, 40, tzinfo=timezone.utc),
    )


def _open_record(service, order):
    record = service.lifecycle_store.get(order.idempotency_key)
    assert not record.terminal
    return record


def _reconcile(service, snap):
    return StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
    ).reconcile(snap)


def test_without_a_lookup_the_aged_out_order_pins_the_reconcile_unhealthy():
    order = intent()
    service = _service(order)
    _open_record(service, order)
    client = MagicMock()
    snap = _adapter(client).capture_reconciliation_snapshot(account_id=order.account_id)
    result = _reconcile(service, snap)
    assert result.healthy is False
    assert result.issues == (f"local_order_missing_broker:{order.idempotency_key}",)
    client.get_order_by_client_id.assert_not_called()


def test_a_cancelled_order_outside_the_window_is_read_and_closed():
    order = intent()
    service = _service(order)
    record = _open_record(service, order)
    client = MagicMock()
    client.get_order_by_client_id.return_value = _raw(order, record)
    snap = _adapter(client).capture_reconciliation_snapshot(
        account_id=order.account_id,
        lookup_client_order_ids=((order.idempotency_key, record.broker_order_id),),
    )
    assert [o.client_order_id for o in snap.orders] == [order.idempotency_key]
    result = _reconcile(service, snap)
    assert result.healthy is True and result.issues == ()
    assert service.lifecycle_store.get(order.idempotency_key).terminal


def test_the_broker_id_is_tried_when_the_client_id_lookup_fails():
    order = intent()
    service = _service(order)
    record = _open_record(service, order)
    client = MagicMock()
    client.get_order_by_client_id.side_effect = RuntimeError("404 not found")
    client.get_order_by_id.return_value = _raw(order, record)
    snap = _adapter(client).capture_reconciliation_snapshot(
        account_id=order.account_id,
        lookup_client_order_ids=((order.idempotency_key, "broker-1"),),
    )
    assert _reconcile(service, snap).healthy is True


def test_a_failed_lookup_changes_nothing_and_still_fails_closed():
    order = intent()
    service = _service(order)
    _open_record(service, order)
    client = MagicMock()
    client.get_order_by_client_id.side_effect = RuntimeError("503")
    client.get_order_by_id.side_effect = RuntimeError("503")
    snap = _adapter(client).capture_reconciliation_snapshot(
        account_id=order.account_id,
        lookup_client_order_ids=((order.idempotency_key, "broker-1"),),
    )
    assert snap.broker_available is True and snap.orders == ()
    assert _reconcile(service, snap).healthy is False


def test_an_answer_for_a_different_order_is_ignored():
    order = intent()
    service = _service(order)
    record = _open_record(service, order)
    wrong = _raw(order, record)
    wrong.client_order_id = "someone-elses-order"
    client = MagicMock()
    client.get_order_by_client_id.return_value = wrong
    client.get_order_by_id.return_value = wrong
    snap = _adapter(client).capture_reconciliation_snapshot(
        account_id=order.account_id,
        lookup_client_order_ids=((order.idempotency_key, "broker-1"),),
    )
    assert snap.orders == ()


def test_an_order_the_walk_already_returned_is_not_looked_up_again():
    order = intent()
    service = _service(order)
    record = _open_record(service, order)
    client = MagicMock()
    adapter = _adapter(client)
    client.get_orders.return_value = [_raw(order, record, status="new")]
    snap = adapter.capture_reconciliation_snapshot(
        account_id=order.account_id,
        lookup_client_order_ids=((order.idempotency_key, "broker-1"),),
    )
    assert len(snap.orders) == 1
    client.get_order_by_client_id.assert_not_called()


def test_the_broker_names_only_its_own_open_orders():
    import live_orders as broker

    order = intent()
    service = _service(order)
    record = _open_record(service, order)
    adapter = _adapter(MagicMock())
    assert broker.reconcile_lookup_kwargs(adapter, service) == {
        "lookup_client_order_ids": ((order.idempotency_key, str(record.broker_order_id or "")),)
    }
    # an adapter without the parameter gets the old call, unchanged
    legacy = SimpleNamespace(capture_reconciliation_snapshot=lambda account_id: None)
    assert broker.reconcile_lookup_kwargs(legacy, service) == {}
    # nothing open, nothing to name
    snap = _adapter(_client_with(order, record)).capture_reconciliation_snapshot(
        account_id=order.account_id,
        lookup_client_order_ids=((order.idempotency_key, ""),),
    )
    _reconcile(service, snap)
    assert broker.reconcile_lookup_kwargs(adapter, service) == {}


def _client_with(order, record):
    client = MagicMock()
    client.get_order_by_client_id.return_value = _raw(order, record)
    return client
