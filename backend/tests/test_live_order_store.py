from __future__ import annotations

from decimal import Decimal

import pytest

from live_orders import (
    InMemoryLifecycleBackend,
    LifecycleConflict,
    LifecycleState,
    OrderLifecycleStore,
)
from live_order_task8_helpers import event, intent


def test_store_is_append_only_and_duplicate_event_id_is_idempotent():
    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    created = store.create_intent(order)
    ack = event(order, state=LifecycleState.ACKNOWLEDGED, event_id="ack-1")

    first = store.append(ack, expected_version=created.version)
    duplicate = store.append(ack, expected_version=created.version)

    assert first.appended is True
    assert duplicate.appended is False
    assert duplicate.record.version == first.record.version
    assert [row.event_id for row in duplicate.record.events].count("ack-1") == 1


def test_store_rejects_sequence_gap_identity_drift_and_terminal_rewrite():
    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    created = store.create_intent(order)

    with pytest.raises(LifecycleConflict, match="expected version"):
        store.append(
            event(order, state=LifecycleState.ACKNOWLEDGED),
            expected_version=created.version + 1,
        )

    with pytest.raises(LifecycleConflict, match="identity"):
        store.append(
            event(
                order,
                state=LifecycleState.ACKNOWLEDGED,
                broker_order_id="broker-drift",
            ).with_identity(account_id="another-account"),
            expected_version=created.version,
        )

    filled = store.append(
        event(
            order,
            state=LifecycleState.FILLED,
            cumulative=Decimal("5"),
            average=Decimal("101"),
        ),
        expected_version=created.version,
    ).record
    with pytest.raises(LifecycleConflict, match="terminal"):
        store.append(
            event(order, state=LifecycleState.CANCELED, event_id="late-cancel"),
            expected_version=filled.version,
        )


def test_two_store_instances_racing_compare_and_set_have_one_winner():
    backend = InMemoryLifecycleBackend()
    left = OrderLifecycleStore(backend)
    right = OrderLifecycleStore(backend)
    order = intent()
    version = left.create_intent(order).version

    left.append(
        event(order, state=LifecycleState.ACKNOWLEDGED, event_id="left"),
        expected_version=version,
    )
    with pytest.raises(LifecycleConflict, match="expected version"):
        right.append(
            event(order, state=LifecycleState.ACKNOWLEDGED, event_id="right"),
            expected_version=version,
        )
