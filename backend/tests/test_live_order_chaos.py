from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from live_orders import (
    Health,
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderLifecycleStore,
)
from live_order_task8_helpers import broker_ref, event, intent, snapshot


@pytest.mark.parametrize(
    "dependency",
    (
        "quote",
        "cash",
        "positions",
        "calendar",
        "persistence",
        "kill_switch",
        "risk_state",
        "watchdog",
    ),
)
def test_unknown_dependency_never_creates_new_exposure(dependency):
    """Removing any fail-closed dependency branch must reach the transport."""
    order = intent()
    calls = []
    unhealthy = replace(
        snapshot(order),
        **{dependency: Health.UNKNOWN},
    )
    result = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda _current: unhealthy,
        transport=lambda **kwargs: calls.append(kwargs),
    ).submit(order)

    assert result.accepted is False
    assert f"dependency.{dependency}.unknown" in result.decision.reason_codes
    assert calls == []


def test_ack_loss_restart_reconciles_without_duplicate_order():
    """Retrying transport while broker truth is unknown must make two posts."""
    order = intent()
    backend = InMemoryLifecycleBackend()
    posts = []
    broker_truth = [None, broker_ref(order)]

    def post(**_kwargs):
        posts.append(order.idempotency_key)
        raise TimeoutError("acknowledgement lost")

    def lookup(_client_order_id):
        return broker_truth.pop(0)

    first = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=post,
        lookup_by_client_id=lookup,
        lifecycle_store=OrderLifecycleStore(backend),
    ).submit(order)
    second = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=post,
        lookup_by_client_id=lookup,
        lifecycle_store=OrderLifecycleStore(backend),
    ).submit(order)

    assert first.uncertain is True
    assert second.accepted is True
    assert posts == [order.idempotency_key]
    assert (
        OrderLifecycleStore(backend)
        .require(order.idempotency_key)
        .state
        is LifecycleState.ACKNOWLEDGED
    )


def test_partial_and_duplicate_fills_manage_owned_quantity_exactly_once():
    """Applying cumulative quantities twice must overstate managed ownership."""
    order = intent()
    owned = Decimal("0")

    def account(fill):
        nonlocal owned
        owned += fill.position_delta

    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=lambda **_kwargs: broker_ref(order),
        confirmed_fill_handler=account,
    )
    service.submit(order)
    partial = event(
        order,
        state=LifecycleState.PARTIAL,
        cumulative=Decimal("2"),
        average=Decimal("100"),
        event_id="partial",
    )
    final = event(
        order,
        state=LifecycleState.FILLED,
        cumulative=Decimal("5"),
        average=Decimal("101"),
        event_id="filled",
    )

    assert service.apply_broker_event(partial).applied is True
    assert service.apply_broker_event(replace(partial, event_id="partial-dup")).applied is False
    assert service.apply_broker_event(final).applied is True
    assert service.apply_broker_event(replace(final, event_id="filled-dup")).applied is False
    assert owned == order.quantity
    assert service.lifecycle_store.require(order.idempotency_key).cumulative_quantity == order.quantity
