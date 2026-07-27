from __future__ import annotations

from live_orders import (
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderLifecycleStore,
)
from live_order_task8_helpers import broker_ref, intent, snapshot


def test_ambiguous_post_is_reconciled_before_any_retry():
    order = intent()
    backend = InMemoryLifecycleBackend()
    store = OrderLifecycleStore(backend)
    posts = []
    lookups = []

    def transport(**_kwargs):
        posts.append("post")
        raise TimeoutError("ack lost")

    broker_truth = [None, broker_ref(order)]

    def lookup(client_order_id):
        lookups.append(client_order_id)
        return broker_truth.pop(0)

    first = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=transport,
        lookup_by_client_id=lookup,
        lifecycle_store=store,
    ).submit(order)
    assert first.uncertain is True
    assert store.require(order.idempotency_key).state is LifecycleState.UNKNOWN

    restarted = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=transport,
        lookup_by_client_id=lookup,
        lifecycle_store=OrderLifecycleStore(backend),
    )
    second = restarted.submit(order)

    assert second.accepted is True
    assert posts == ["post"]
    assert len(lookups) == 2
    assert store.require(order.idempotency_key).state is LifecycleState.ACKNOWLEDGED


def test_persist_failure_prevents_transport():
    order = intent()
    calls = []

    class BrokenBackend(InMemoryLifecycleBackend):
        def create(self, row):
            raise OSError("disk unavailable")

    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=lambda **kwargs: calls.append(kwargs),
        lifecycle_store=OrderLifecycleStore(BrokenBackend()),
    )

    submission = service.submit(order)

    assert submission.accepted is False
    assert submission.decision.reason_codes == ("persistence.intent.failed",)
    assert calls == []
