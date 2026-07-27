from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from live_orders import (
    AuthoritativeBrokerSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderLifecycleStore,
    StartupReconciler,
)
from live_order_task8_helpers import broker_ref, intent, snapshot


NOW = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)


def _service(order=None, *, fills=None):
    order = order or intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=lambda **_kwargs: broker_ref(order),
        lifecycle_store=store,
        confirmed_fill_handler=(fills if fills is not None else []).append,
    )
    service.submit(order)
    return service


def _broker_snapshot(*, positions=(), orders=(), **changes):
    values = {
        "account_id": "acct-1",
        "instance_id": "instance-1",
        "observed_at": NOW,
        "positions": tuple(positions),
        "orders": tuple(orders),
        "broker_available": True,
        "positions_stable": True,
        "orders_complete": True,
    }
    values.update(changes)
    return AuthoritativeBrokerSnapshot(**values)


def _position(symbol, quantity):
    return BrokerPositionSnapshot(
        symbol=symbol,
        quantity=Decimal(str(quantity)),
        market_value=Decimal(str(quantity)) * Decimal("100"),
    )


def _order(order, *, status, filled, average=Decimal("100")):
    return BrokerOrderSnapshot(
        client_order_id=order.idempotency_key,
        broker_order_id="broker-1",
        symbol=order.symbol,
        side=order.side,
        requested_quantity=order.quantity,
        status=status,
        cumulative_quantity=Decimal(str(filled)),
        cumulative_average_price=average if filled else None,
        cumulative_fees=Decimal("0"),
        updated_at=NOW,
    )


def test_fill_missing_locally_is_owned_once_after_restart():
    order = intent(quantity=Decimal("4"))
    fills = []
    service = _service(order, fills=fills)
    reconciler = StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
    )
    broker = _broker_snapshot(
        positions=(_position("AAPL", 4),),
        orders=(_order(order, status="filled", filled=4),),
    )

    first = reconciler.reconcile(broker)
    second = reconciler.reconcile(broker)

    assert first.healthy is True
    assert dict(first.owned) == {"AAPL": Decimal("4")}
    assert dict(first.external) == {}
    assert service.lifecycle_store.require(order.idempotency_key).state is LifecycleState.FILLED
    assert [fill.incremental_quantity for fill in fills] == [Decimal("4")]
    assert second.evidence_hash == first.evidence_hash
    assert [fill.incremental_quantity for fill in fills] == [Decimal("4")]


def test_manual_position_while_stopped_is_external_not_adopted():
    result = StartupReconciler(
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        event_applier=lambda _event: None,
    ).reconcile(
        _broker_snapshot(positions=(_position("MSFT", 7),))
    )

    assert result.healthy is True
    assert dict(result.owned) == {}
    assert dict(result.external) == {"MSFT": Decimal("7")}


def test_partial_strategy_position_splits_external_excess():
    order = intent(quantity=Decimal("3"))
    service = _service(order)
    broker = _broker_snapshot(
        positions=(_position("AAPL", 5),),
        orders=(_order(order, status="filled", filled=3),),
    )

    result = StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
    ).reconcile(broker)

    assert result.healthy is True
    assert dict(result.owned) == {"AAPL": Decimal("3")}
    assert dict(result.external) == {"AAPL": Decimal("2")}


@pytest.mark.parametrize(
    "changes",
    [
        {"broker_available": False},
        {"positions_stable": False},
        {"orders_complete": False},
    ],
)
def test_incomplete_broker_truth_quarantines_everything(changes):
    result = StartupReconciler(
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        event_applier=lambda _event: None,
    ).reconcile(
        _broker_snapshot(positions=(_position("AAPL", 5),), **changes)
    )

    assert result.healthy is False
    assert dict(result.owned) == {}
    assert dict(result.external) == {}
    assert dict(result.unresolved) == {"AAPL": Decimal("5")}


def test_manual_sell_below_confirmed_lineage_is_unresolved_and_blocks_adds():
    order = intent(quantity=Decimal("5"))
    service = _service(order)
    result = StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
    ).reconcile(
        _broker_snapshot(
            positions=(_position("AAPL", 3),),
            orders=(_order(order, status="filled", filled=5),),
        )
    )

    assert result.healthy is False
    assert dict(result.owned) == {"AAPL": Decimal("3")}
    assert dict(result.unresolved) == {"AAPL": Decimal("2")}


def test_adapter_defers_clean_room_ownership_until_result_is_applied():
    from broker_adapters._wal import InMemoryStore, LiveOrderWAL
    from broker_adapters.alpaca import AlpacaAdapter

    position = MagicMock(
        symbol="AAPL",
        qty="4",
        market_value="400",
        avg_entry_price="100",
        unrealized_pl="0",
    )
    client = MagicMock()
    client.get_account.return_value = MagicMock(
        cash="1000", buying_power="1000", daytrading_buying_power="1000",
        equity="1400", last_equity="1400", pattern_day_trader=False,
        daytrade_count=0, account_blocked=False, trading_blocked=False,
    )
    client.get_all_positions.return_value = [position]
    client._session = None
    adapter = AlpacaAdapter(
        api_key="k",
        api_secret="s",
        paper=True,
        instance_id="instance-1",
        wal=LiveOrderWAL(InMemoryStore()),
        initial_value=1400,
        seed_trades_from_broker=False,
        clean_room_mode=True,
        defer_ownership_reconciliation=True,
        _test_client=client,
    )

    assert adapter._positions == {}
    assert adapter._reconciliation_healthy is False
    assert adapter.get_external_positions()["AAPL"]["qty"] == 4.0

    result = StartupReconciler(
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        event_applier=lambda _event: None,
    ).reconcile(
        _broker_snapshot(positions=(_position("AAPL", 4),))
    )
    adapter.complete_startup_reconciliation(result)

    assert adapter._positions == {}
    assert adapter._reconciliation_healthy is True
    assert adapter.get_external_positions()["AAPL"]["qty"] == 4.0


def test_adapter_captures_stable_complete_broker_snapshot():
    from broker_adapters._wal import InMemoryStore, LiveOrderWAL
    from broker_adapters.alpaca import AlpacaAdapter

    position = MagicMock(
        symbol="AAPL",
        qty="4",
        market_value="400",
        avg_entry_price="100",
        unrealized_pl="0",
    )
    account = MagicMock(
        cash="1000", buying_power="1000", daytrading_buying_power="1000",
        equity="1400", last_equity="1400", pattern_day_trader=False,
        daytrade_count=0, account_blocked=False, trading_blocked=False,
    )
    client = MagicMock()
    client.get_account.return_value = account
    client.get_all_positions.return_value = [position]
    client.get_orders.return_value = []
    client._session = None
    adapter = AlpacaAdapter(
        api_key="k", api_secret="s", paper=True, instance_id="instance-1",
        wal=LiveOrderWAL(InMemoryStore()), initial_value=1400,
        seed_trades_from_broker=False, _test_client=client,
    )

    captured = adapter.capture_reconciliation_snapshot(account_id="acct-1")

    assert captured.broker_available is True
    assert captured.positions_stable is True
    assert captured.orders_complete is True
    assert captured.positions[0].quantity == Decimal("4")
