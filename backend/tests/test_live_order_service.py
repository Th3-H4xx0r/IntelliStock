from __future__ import annotations

from decimal import Decimal
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from live_orders import (
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderLifecycleStore,
)
from live_order_task8_helpers import broker_ref, event, intent, snapshot


def test_intent_is_durable_before_transport_and_ack_keeps_reservation():
    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    observed = []

    def transport(**_kwargs):
        observed.append(store.require(order.idempotency_key).state)
        return broker_ref(order)

    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=transport,
        lifecycle_store=store,
    )
    submission = service.submit(order)

    assert submission.accepted is True
    assert observed == [LifecycleState.SUBMITTING]
    assert store.require(order.idempotency_key).state is LifecycleState.ACKNOWLEDGED
    assert service.reservation_for(order.idempotency_key).remaining_quantity == Decimal("5")
    assert service.reservation_for(order.idempotency_key).remaining_notional == Decimal("500")


def test_confirmed_sell_fill_alone_applies_proceeds_and_fees():
    order = intent(
        side="sell",
        reduce_only=True,
        quantity=Decimal("3"),
    )
    fills = []
    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=lambda **_kwargs: broker_ref(order),
        confirmed_fill_handler=fills.append,
    )
    service.submit(order)
    assert fills == []

    applied = service.apply_broker_event(
        event(
            order,
            state=LifecycleState.FILLED,
            cumulative=Decimal("3"),
            average=Decimal("99"),
            fees=Decimal("1.25"),
        )
    )

    assert applied.applied is True
    assert applied.fill.incremental_quantity == Decimal("3")
    assert applied.fill.cash_delta == Decimal("295.75")
    assert fills == [applied.fill]
    assert service.reservation_for(order.idempotency_key) is None


def test_alpaca_stream_callback_only_dispatches_normalized_event():
    from broker_adapters._wal import InMemoryStore, LiveOrderWAL
    from broker_adapters.alpaca import AlpacaAdapter

    client = MagicMock()
    client.get_account.return_value = MagicMock(
        cash="1000", buying_power="1000", daytrading_buying_power="1000",
        equity="1000", last_equity="1000", pattern_day_trader=False,
        daytrade_count=0, account_blocked=False, trading_blocked=False,
    )
    client.get_all_positions.return_value = []
    client._session = None
    adapter = AlpacaAdapter(
        api_key="k",
        api_secret="s",
        paper=True,
        instance_id="instance-1",
        wal=LiveOrderWAL(InMemoryStore()),
        initial_value=1000,
        seed_trades_from_broker=False,
        _test_client=client,
    )
    received = []
    dispatched = threading.Event()

    def sink(normalized):
        received.append(normalized)
        dispatched.set()

    adapter.bind_order_event_sink(sink, account_id="acct-1")
    before_cash = adapter._cash
    before_positions = dict(adapter._positions)
    order = SimpleNamespace(
        client_order_id="instance-cid",
        symbol="AAPL",
        filled_qty="2",
        filled_avg_price="101",
        side="buy",
        id="broker-1",
    )
    asyncio.run(
        adapter._on_trade_update(
            SimpleNamespace(event="partial_fill", order=order, message="")
        )
    )

    assert dispatched.wait(timeout=1)
    assert received[0].state is LifecycleState.PARTIAL
    assert received[0].cumulative_quantity == Decimal("2")
    assert adapter._cash == before_cash
    assert adapter._positions == before_positions
