from __future__ import annotations

from decimal import Decimal

import pytest

from live_orders import (
    LifecycleState,
    LiveOrderService,
    TerminalRetryExhausted,
    new_retry_intent,
)
from live_order_task8_helpers import broker_ref, event, intent, snapshot


def test_terminal_retry_has_new_identity_and_is_bounded():
    original = intent()
    service = LiveOrderService(
        account_id=original.account_id,
        instance_id=original.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=lambda **_kwargs: broker_ref(original),
        max_terminal_retries=1,
    )
    service.submit(original)
    service.apply_broker_event(
        event(
            original,
            state=LifecycleState.REJECTED,
            reason="venue rejection",
        )
    )

    retry = new_retry_intent(original, reason="bounded retry", maximum=1)

    assert retry.retry_ordinal == 1
    assert retry.idempotency_key != original.idempotency_key
    assert service.lifecycle_store.require(original.idempotency_key).state is LifecycleState.REJECTED
    with pytest.raises(TerminalRetryExhausted):
        new_retry_intent(retry, reason="again", maximum=1)


def test_partial_then_cancel_accounts_partial_only_and_releases_remainder():
    order = intent(quantity=Decimal("5"))
    fills = []
    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=lambda **_kwargs: broker_ref(order),
        confirmed_fill_handler=fills.append,
    )
    service.submit(order)
    service.apply_broker_event(
        event(
            order,
            state=LifecycleState.PARTIAL,
            cumulative=Decimal("2"),
            average=Decimal("100"),
        )
    )
    service.apply_broker_event(
        event(
            order,
            state=LifecycleState.CANCELED,
            cumulative=Decimal("2"),
            average=Decimal("100"),
            event_id="canceled-after-partial",
        )
    )

    assert [fill.incremental_quantity for fill in fills] == [Decimal("2")]
    assert service.reservation_for(order.idempotency_key) is None
