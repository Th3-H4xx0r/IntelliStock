"""A rejection, a lost POST, or a re-emitted decision must never end with the
bot unable to exit a real position."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from broker_adapters.errors import BrokerError, PDTRestricted
from live_orders import (
    AuthoritativeBrokerSnapshot,
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSide,
    StartupReconciler,
)
from live_order_task8_helpers import NOW, broker_ref, intent, snapshot


def _service(*, transport, store=None, lookup=None, max_terminal_retries=2):
    order = intent()
    return LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=transport,
        lookup_by_client_id=lookup,
        lifecycle_store=store or OrderLifecycleStore(InMemoryLifecycleBackend()),
        max_terminal_retries=max_terminal_retries,
    )


def _empty_broker_snapshot(order, **changes):
    # Lifecycle events written by the service are stamped with wall-clock time,
    # and the abandonment grace period compares against those, so the snapshot
    # has to observe the account "now" rather than at the fixture's NOW.
    values = {
        "account_id": order.account_id,
        "instance_id": order.instance_id,
        "observed_at": datetime.now(timezone.utc) + timedelta(minutes=30),
        "positions": (),
        "orders": (),
        "broker_available": True,
        "positions_stable": True,
        "orders_complete": True,
    }
    values.update(changes)
    return AuthoritativeBrokerSnapshot(**values)


def test_definite_rejection_is_terminal_not_unknown():
    """A PDT rejection used to land in UNKNOWN, which nothing could resolve."""

    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())

    def transport(**_kwargs):
        raise PDTRestricted("equity < $25k and day_trade_count=3")

    submission = _service(
        transport=transport, store=store, lookup=lambda _cid: None
    ).submit(order)

    record = store.require(order.idempotency_key)
    assert record.state is LifecycleState.REJECTED
    assert record.terminal is True
    assert submission.uncertain is False
    assert submission.accepted is False
    assert submission.decision.reason_codes == ("broker.rejected.PDTRestricted",)


def test_ambiguous_transport_failure_stays_unknown():
    """The inverse must still hold: an unproven outcome is never terminal."""

    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())

    def transport(**_kwargs):
        raise BrokerError("submit_order ambiguous result")

    submission = _service(
        transport=transport, store=store, lookup=lambda _cid: None
    ).submit(order)

    assert store.require(order.idempotency_key).state is LifecycleState.UNKNOWN
    assert submission.uncertain is True


def test_rejected_exit_can_be_re_emitted_under_a_fresh_identity():
    """The lockout that mattered: a rejected SELL that can never be retried is
    a position that can never be closed."""

    exit_order = intent(
        side=OrderSide.SELL, reduce_only=True, quantity=Decimal("4")
    )
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    attempts = []

    def transport(**kwargs):
        attempts.append(kwargs["client_order_id"])
        if len(attempts) == 1:
            raise PDTRestricted("first attempt refused")
        return broker_ref(exit_order, broker_order_id="broker-exit")

    service = _service(transport=transport, store=store, lookup=lambda _cid: None)

    first = service.submit(exit_order)
    assert first.accepted is False

    second = service.submit(exit_order)

    assert second.accepted is True
    assert len(attempts) == 2
    assert attempts[0] != attempts[1]
    assert store.require(attempts[1]).state is LifecycleState.ACKNOWLEDGED


def test_retry_escalation_is_bounded_and_never_resubmits_a_filled_order():
    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    attempts = []

    def transport(**kwargs):
        attempts.append(kwargs["client_order_id"])
        raise PDTRestricted("refused")

    service = _service(
        transport=transport,
        store=store,
        lookup=lambda _cid: None,
        max_terminal_retries=1,
    )
    service.submit(order)
    service.submit(order)
    exhausted = service.submit(order)

    assert len(attempts) == 2
    assert exhausted.decision.reason_codes == (
        "idempotency.terminal_retry_exhausted",
    )


def test_filled_order_is_never_retried_by_the_escalation():
    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    posts = []

    def transport(**kwargs):
        posts.append(kwargs["client_order_id"])
        return broker_ref(
            order,
            status="filled",
            filled_qty=order.quantity,
            filled_avg_price=Decimal("100"),
        )

    service = _service(transport=transport, store=store)
    service.submit(order)
    repeated = service.submit(order)

    assert posts == [order.idempotency_key]
    assert repeated.decision.reason_codes == (
        "idempotency.terminal_requires_retry",
    )


def test_reemitted_buy_keeps_one_identity_across_drifted_sizing():
    """A restart replays the decision on a different bar with a different risk
    snapshot and slightly different sizing. That is one buy, not two."""

    morning = intent(quantity=Decimal("5"), reference_price=Decimal("100"))
    replayed = intent(
        quantity=Decimal("5.13"),
        reference_price=Decimal("101"),
        decision_at=NOW + timedelta(hours=2),
        quote_at=NOW + timedelta(hours=2),
        risk_snapshot_id="risk-state:412:later",
    )

    assert replayed.idempotency_key == morning.idempotency_key

    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    posts = []

    def transport(**kwargs):
        posts.append(kwargs["client_order_id"])
        return broker_ref(morning)

    service = _service(
        transport=transport,
        store=store,
        lookup=lambda _cid: broker_ref(morning),
    )
    service.submit(morning)
    second = service.submit(replayed)

    assert posts == [morning.idempotency_key]
    assert second.accepted is True


def test_next_session_buy_is_a_new_identity():
    today = intent()
    tomorrow = intent(
        decision_at=NOW + timedelta(days=1), quote_at=NOW + timedelta(days=1)
    )

    assert tomorrow.idempotency_key != today.idempotency_key


def test_exit_identity_re_mints_every_minute():
    """Sells must dedupe within a tick and never longer: a suppressed exit is
    worse than an extra one, which reduce-only already caps at the position."""

    first = intent(side=OrderSide.SELL, reduce_only=True, quantity=Decimal("4"))
    same_tick = intent(
        side=OrderSide.SELL,
        reduce_only=True,
        quantity=Decimal("4"),
        decision_at=NOW + timedelta(seconds=20),
        quote_at=NOW + timedelta(seconds=20),
        risk_snapshot_id="risk-state:9:later",
    )
    next_minute = intent(
        side=OrderSide.SELL,
        reduce_only=True,
        quantity=Decimal("4"),
        decision_at=NOW + timedelta(minutes=1),
        quote_at=NOW + timedelta(minutes=1),
    )

    assert same_tick.idempotency_key == first.idempotency_key
    assert next_minute.idempotency_key != first.idempotency_key


def test_abandoned_order_retires_instead_of_pinning_the_cycle_unhealthy():
    """An UNKNOWN row the broker never heard of made every later reconcile
    unhealthy, and an unhealthy reconcile skips the cycle — risk exits too."""

    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())

    def transport(**_kwargs):
        raise BrokerError("connection reset")

    service = _service(transport=transport, store=store, lookup=lambda _cid: None)
    service.submit(order)
    assert store.require(order.idempotency_key).state is LifecycleState.UNKNOWN

    result = StartupReconciler(
        lifecycle_store=store, event_applier=service.apply_broker_event
    ).reconcile(_empty_broker_snapshot(order))

    assert result.issues == ()
    assert result.healthy is True
    assert store.require(order.idempotency_key).state is LifecycleState.EXPIRED
    assert service.reservation_for(order.idempotency_key) is None


def test_abandoned_retirement_waits_out_its_grace_period():
    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())

    def transport(**_kwargs):
        raise BrokerError("connection reset")

    service = _service(transport=transport, store=store, lookup=lambda _cid: None)
    service.submit(order)

    result = StartupReconciler(
        lifecycle_store=store,
        event_applier=service.apply_broker_event,
        abandoned_grace=timedelta(days=1),
    ).reconcile(_empty_broker_snapshot(order))

    assert result.healthy is False
    assert any(
        issue.startswith("local_order_missing_broker") for issue in result.issues
    )
    assert store.require(order.idempotency_key).state is LifecycleState.UNKNOWN


def test_acknowledged_order_missing_from_broker_stays_an_issue():
    """Retiring rows the broker DID acknowledge would hide a real discrepancy,
    so only never-acknowledged, zero-fill rows qualify."""

    order = intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    service = _service(
        transport=lambda **_kwargs: broker_ref(order), store=store
    )
    service.submit(order)
    assert store.require(order.idempotency_key).broker_order_id == "broker-1"

    result = StartupReconciler(
        lifecycle_store=store, event_applier=service.apply_broker_event
    ).reconcile(_empty_broker_snapshot(order))

    assert result.healthy is False
    assert result.issues == (
        f"local_order_missing_broker:{order.idempotency_key}",
    )
