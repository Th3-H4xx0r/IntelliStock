from __future__ import annotations

from decimal import Decimal

from live_orders import LifecycleState, LiveOrderService
from live_order_task8_helpers import broker_ref, event, intent, snapshot


def test_cumulative_partial_fills_are_converted_to_exactly_once_deltas():
    order = intent()
    fills = []
    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=lambda **_kwargs: broker_ref(order),
        confirmed_fill_handler=fills.append,
    )
    service.submit(order)

    first = service.apply_broker_event(
        event(
            order,
            state=LifecycleState.PARTIAL,
            cumulative=Decimal("2"),
            average=Decimal("100"),
            event_id="partial-2",
        )
    )
    duplicate = service.apply_broker_event(
        event(
            order,
            state=LifecycleState.PARTIAL,
            cumulative=Decimal("2"),
            average=Decimal("100"),
            event_id="partial-2-duplicate-id",
        )
    )
    final = service.apply_broker_event(
        event(
            order,
            state=LifecycleState.FILLED,
            cumulative=Decimal("5"),
            average=Decimal("101"),
            event_id="fill-5",
        )
    )
    repeated = service.apply_broker_event(
        event(
            order,
            state=LifecycleState.FILLED,
            cumulative=Decimal("5"),
            average=Decimal("101"),
            event_id="fill-5-repeat",
        )
    )

    assert first.fill.incremental_quantity == Decimal("2")
    assert duplicate.applied is False
    assert final.fill.incremental_quantity == Decimal("3")
    assert final.fill.incremental_price.quantize(Decimal("0.01")) == Decimal("101.67")
    assert repeated.applied is False
    assert [fill.incremental_quantity for fill in fills] == [
        Decimal("2"),
        Decimal("3"),
    ]


def test_gap_fill_marks_breach_without_consuming_another_reservation():
    first_order = intent()
    second_order = intent(symbol="MSFT", risk_snapshot_id="risk-2")
    service = LiveOrderService(
        account_id=first_order.account_id,
        instance_id=first_order.instance_id,
        snapshot_provider=lambda current: snapshot(
            current,
            quote_symbol=current.symbol,
            position_symbol=current.symbol,
        ),
        transport=lambda **kwargs: broker_ref(
            first_order if kwargs["symbol"] == "AAPL" else second_order,
            broker_order_id=f"broker-{kwargs['symbol']}",
        ),
    )
    service.submit(first_order)
    service.submit(second_order)
    second_before = service.reservation_for(
        second_order.idempotency_key
    ).remaining_notional

    service.apply_broker_event(
        event(
            first_order,
            state=LifecycleState.FILLED,
            cumulative=Decimal("5"),
            average=Decimal("150"),
            broker_order_id="broker-AAPL",
        )
    )

    assert first_order.idempotency_key in service.capacity_breaches
    assert (
        service.reservation_for(second_order.idempotency_key).remaining_notional
        == second_before
    )
