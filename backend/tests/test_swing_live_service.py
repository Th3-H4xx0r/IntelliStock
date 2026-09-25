"""swing-port Task 7: service transport extras, contract-multiplier cash,
bracket-leg lifecycle rows and external fills; reconcile's held and
pending_cancel statuses and short-option ownership.

EB pins computed from the PRE-change code on 2026-09-24. Never edit them."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from live_orders import (
    AuthoritativeBrokerSnapshot,
    BrokerOrderEvent,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    InMemoryLifecycleBackend,
    LifecycleConflict,
    LifecycleState,
    LiveOrderService,
    OrderIntent,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
    StartupReconciler,
    TerminalRetryExhausted,
    new_retry_intent,
)
from live_orders.service import bracket_leg_intent
from live_order_task8_helpers import broker_ref, event, intent, snapshot
from swing_live_fixtures import (
    OCC,
    RTH,
    bracket_intent,
    buy_to_close,
    equity_snapshot,
    leg_ref,
    option_intent,
    option_snapshot,
    parent_ref,
)

EB_BUY_KWARGS = {
    "symbol": "TQQQ", "side": "buy", "qty": 5.0, "notional": None,
    "order_type": "market", "limit_price": None, "tif": "day",
    "extended_hours": False,
    "client_order_id": "instance-efac83a02fb2f5054a31ae56985e212a45729-0",
}
EB_SELL_KWARGS = {
    "symbol": "TQQQ", "side": "sell", "qty": 3.0, "notional": None,
    "order_type": "limit", "limit_price": 99.5, "tif": "day",
    "extended_hours": True,
    "client_order_id": "instance-f66835a1faf331e09b98f197e71b558826058-0",
}
EB_RECONCILE_HASH = (
    "a416811574d648504d6c40c999308365a8c8b43801e790fa349b52308173834b")


def _service(order, *, transport=None, provider=None, store=None, **kwargs):
    return LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=provider, transport=transport,
        lifecycle_store=store or OrderLifecycleStore(InMemoryLifecycleBackend()),
        **kwargs)


def _recording(order):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return broker_ref(order)

    return calls, transport


def _digest(rows):
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"),
                   default=str).encode()
    ).hexdigest()


# --- EB invariance ------------------------------------------------------------

def test_eb_transport_kwargs_are_byte_identical():
    buy = intent(symbol="TQQQ", reason="eb_rebalance")
    calls, transport = _recording(buy)
    _service(buy, transport=transport, provider=snapshot).submit(buy)
    assert calls == [EB_BUY_KWARGS]
    sell = intent(symbol="TQQQ", reason="eb_rotation_trim",
                  side=OrderSide.SELL, quantity=Decimal("3"),
                  reduce_only=True, order_type="limit",
                  limit_price=Decimal("99.5"), extended_hours=True)
    calls, transport = _recording(sell)
    _service(sell, transport=transport, provider=snapshot).submit(sell)
    assert calls == [EB_SELL_KWARGS]


def test_an_eb_shaped_reconcile_is_byte_identical():
    at = datetime(2026, 9, 24, 13, 31, 5, tzinfo=timezone.utc)
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    buy = OrderIntent(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        source=OrderSource.STRATEGY, reason="eb_rebalance", symbol="TQQQ",
        side=OrderSide.BUY, quantity=Decimal("10"), reduce_only=False,
        decision_at=at, quote_at=at, risk_snapshot_id="risk-1",
        reference_price=Decimal("88"))
    store.create_intent(buy)
    service = LiveOrderService(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        snapshot_provider=None, transport=None, lifecycle_store=store)
    service.apply_broker_event(BrokerOrderEvent(
        event_id="ack-1", account_id="brk-alpaca-main",
        instance_id="alpaca-main", client_order_id=buy.idempotency_key,
        broker_order_id="b-1", symbol="TQQQ", side=OrderSide.BUY,
        state=LifecycleState.ACKNOWLEDGED, cumulative_quantity=Decimal("0"),
        cumulative_average_price=None, cumulative_fees=Decimal("0"),
        occurred_at=at))
    orders = (
        BrokerOrderSnapshot(
            client_order_id=buy.idempotency_key, broker_order_id="b-1",
            symbol="TQQQ", side=OrderSide.BUY,
            requested_quantity=Decimal("10"), status="filled",
            cumulative_quantity=Decimal("10"),
            cumulative_average_price=Decimal("88"),
            cumulative_fees=Decimal("0"), updated_at=at),
        BrokerOrderSnapshot(
            client_order_id="manual-xyz", broker_order_id="b-2",
            symbol="SPY", side=OrderSide.BUY, requested_quantity=Decimal("2"),
            status="filled", cumulative_quantity=Decimal("2"),
            cumulative_average_price=Decimal("500"),
            cumulative_fees=Decimal("0"), updated_at=at),
    )
    snap = AuthoritativeBrokerSnapshot(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        observed_at=at,
        positions=(
            BrokerPositionSnapshot(symbol="TQQQ", quantity=Decimal("10"),
                                   market_value=Decimal("880")),
            BrokerPositionSnapshot(symbol="SPY", quantity=Decimal("2"),
                                   market_value=Decimal("1000"))),
        orders=orders, broker_available=True, positions_stable=True,
        orders_complete=True)
    result = StartupReconciler(
        lifecycle_store=store, event_applier=service.apply_broker_event,
        cid_prefix="alpacama-").reconcile(snap)
    assert dict(result.owned) == {"TQQQ": Decimal("10")}
    assert dict(result.external) == {"SPY": Decimal("2")}
    assert result.healthy is True and result.issues == ()
    assert result.evidence_hash == EB_RECONCILE_HASH


def test_the_alpaca_runtime_defers_to_the_lifecycle_reconciler():
    """The legacy _classifier.py quarantines every short; it is safe to leave
    only while no Alpaca runtime takes that path."""
    source = (Path(__file__).resolve().parents[1] / "broker.py").read_text()
    assert "defer_ownership_reconciliation=_is_alpaca_runtime" in source


# The EB service grid: every EB source and order shape through submit, a
# partial and a final fill with fees, and every denial path.

_EB_SHAPES = (
    dict(source=OrderSource.STRATEGY, reason="eb_rebalance"),
    dict(source=OrderSource.MANUAL, reason="manual buy",
         quantity=Decimal("2.5")),
    dict(source=OrderSource.RESIDUAL_SLEEVE, reason="sleeve",
         reference_price=None),
    dict(source=OrderSource.RISK_EXIT, reason="drawdown", side=OrderSide.SELL,
         quantity=Decimal("4"), reduce_only=True),
    dict(source=OrderSource.STRATEGY, reason="eb_rotation_trim",
         side=OrderSide.SELL, quantity=Decimal("3"), reduce_only=True,
         order_type="limit", limit_price=Decimal("99.5"),
         extended_hours=True),
    dict(source=OrderSource.STRATEGY, reason="eb_limit_buy",
         order_type="limit", limit_price=Decimal("100.25"), tif="gtc"),
)


class _Rejected(RuntimeError):
    broker_definitive_rejection = True


def _eb_service_rows():
    rows = []
    for shape in _EB_SHAPES:
        order = intent(symbol="TQQQ", **shape)
        calls, transport = _recording(order)
        fills, events = [], []
        service = _service(
            order, transport=transport, provider=snapshot,
            confirmed_fill_handler=fills.append,
            event_handler=lambda e, f: events.append((e, f)))
        submission = service.submit(order)
        reservation = service.reservation_for(order.idempotency_key)
        half = (order.quantity / 2).quantize(Decimal("0.01"))
        applied = [
            service.apply_broker_event(event(
                order, state=LifecycleState.PARTIAL, cumulative=half,
                average=Decimal("100.10"), fees=Decimal("0.01"))),
            service.apply_broker_event(event(
                order, state=LifecycleState.FILLED, cumulative=order.quantity,
                average=Decimal("100.30"), fees=Decimal("0.05"))),
            service.apply_broker_event(event(
                order, state=LifecycleState.FILLED, cumulative=order.quantity,
                average=Decimal("100.30"), fees=Decimal("0.05"))),
        ]
        record = service.lifecycle_store.require(order.idempotency_key)
        rows.append({
            "kwargs": calls,
            "decision": [submission.decision.allowed,
                         submission.decision.approved_quantity,
                         submission.decision.reason_codes,
                         submission.accepted, submission.uncertain],
            "reservation": (
                None if reservation is None else
                [reservation.side.value, reservation.remaining_quantity,
                 reservation.remaining_notional]),
            "applied": [[a.applied, a.reason] for a in applied],
            "fills": [
                [f.incremental_quantity, f.incremental_price,
                 f.incremental_fees, f.position_delta, f.cash_delta,
                 f.asset_class, f.contract_multiplier, f.event.event_id,
                 f.event.sequence]
                for f in fills],
            "events": [[e.state.value, e.sequence, e.incremental_quantity,
                        f is not None] for e, f in events],
            "record": [record.state.value, record.version,
                       record.cumulative_quantity,
                       record.cumulative_average_price,
                       record.broker_order_id],
            "after": service.reservation_for(order.idempotency_key),
            "breaches": sorted(service.capacity_breaches),
        })
    # Denial paths.
    order = intent(symbol="TQQQ")
    for provider, transport in (
        (lambda _i: (_ for _ in ()).throw(RuntimeError("down")), None),
        (lambda _i: "not a snapshot", None),
        (lambda i: snapshot(i, available_cash=Decimal("1")), None),
        (snapshot, lambda **kw: (_ for _ in ()).throw(_Rejected("pdt"))),
        (snapshot, lambda **kw: (_ for _ in ()).throw(TimeoutError("lost"))),
    ):
        service = _service(order, transport=transport, provider=provider)
        submission = service.submit(order)
        record = service.lifecycle_store.get(order.idempotency_key)
        rows.append([submission.decision.reason_codes, submission.uncertain,
                     None if record is None else record.state.value,
                     service.reservation_for(order.idempotency_key)])
    # A restart rebuilds reservations from the store.
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    for shape in _EB_SHAPES:
        order = intent(symbol="TQQQ", **shape)
        _service(order, transport=_recording(order)[1], provider=snapshot,
                 store=store).submit(order)
    restarted = _service(intent(), store=store)
    rows.append(sorted(
        [key, r.side.value, r.remaining_quantity, r.remaining_notional]
        for key, r in restarted._reservations.items()))
    return rows


EB_SERVICE_SHA256 = (
    "25982a004981d57d9d562157fe13bc6afc0d7a3df3b2498626bfbc8eeb883939")


def test_eb_service_submissions_and_fills_are_byte_identical():
    assert _digest(_eb_service_rows()) == EB_SERVICE_SHA256


# The EB reconcile grid: every non-swing broker status against every local
# state, positions matching, above, below and absent, an equity short, a
# manual order, with and without the instance prefix, inside and past the
# abandoned-order grace.

_AT = datetime(2026, 9, 24, 13, 31, 5, tzinfo=timezone.utc)
_EB_STATUSES = ("new", "accepted", "pending_new", "partially_filled",
                "partial_fill", "filled", "canceled", "cancelled", "rejected",
                "expired", "done_for_day", "replaced", "stopped", "suspended",
                "calculated", "pending_replace", None)
_EB_LOCAL = ("intent", "submitting", "unknown", "acknowledged", "partial",
             "filled", "canceled")


def _eb_book(local, side):
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    order = OrderIntent(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        source=(OrderSource.STRATEGY if side is OrderSide.BUY
                else OrderSource.RISK_EXIT),
        reason="eb", symbol="TQQQ", side=side, quantity=Decimal("10"),
        reduce_only=side is OrderSide.SELL, decision_at=_AT, quote_at=_AT,
        risk_snapshot_id="risk-1", reference_price=Decimal("88"))
    record = store.create_intent(order)
    steps = {
        "intent": (),
        "submitting": ((LifecycleState.SUBMITTING, "0", None, None),),
        "unknown": ((LifecycleState.SUBMITTING, "0", None, None),
                    (LifecycleState.UNKNOWN, "0", None, None)),
        "acknowledged": ((LifecycleState.ACKNOWLEDGED, "0", None, "b-1"),),
        "partial": ((LifecycleState.ACKNOWLEDGED, "0", None, "b-1"),
                    (LifecycleState.PARTIAL, "4", "88", "b-1")),
        "filled": ((LifecycleState.ACKNOWLEDGED, "0", None, "b-1"),
                   (LifecycleState.FILLED, "10", "88", "b-1")),
        "canceled": ((LifecycleState.ACKNOWLEDGED, "0", None, "b-1"),
                     (LifecycleState.CANCELED, "0", None, "b-1")),
    }[local]
    for state, cumulative, average, broker_id in steps:
        record = store.append(BrokerOrderEvent(
            event_id=f"{state.value}-{record.version + 1}",
            account_id=order.account_id, instance_id=order.instance_id,
            client_order_id=order.idempotency_key, broker_order_id=broker_id,
            symbol="TQQQ", side=side, state=state,
            cumulative_quantity=Decimal(cumulative),
            cumulative_average_price=(Decimal(average) if average else None),
            cumulative_fees=Decimal("0"), occurred_at=_AT,
            sequence=record.version + 1), expected_version=record.version,
        ).record
    return order, store


_EB_FILLED = {"partially_filled": "6", "partial_fill": "6", "filled": "10",
              "canceled": "4", "cancelled": "0", "expired": "0",
              "done_for_day": "4"}


def _eb_case(side, local, status, filled, held, prefix, observed):
    """One EB-shaped book reconciled once; the row the grid pins."""
    order, store = _eb_book(local, side)
    service = _service(order, store=store)
    orders = [BrokerOrderSnapshot(
        client_order_id="manual-xyz", broker_order_id="b-2", symbol="SPY",
        side=OrderSide.BUY, requested_quantity=Decimal("2"), status="filled",
        cumulative_quantity=Decimal("2"),
        cumulative_average_price=Decimal("500"),
        cumulative_fees=Decimal("0"), updated_at=_AT)]
    if status is not None:
        orders.append(BrokerOrderSnapshot(
            client_order_id=order.idempotency_key, broker_order_id="b-1",
            symbol="TQQQ", side=side, requested_quantity=Decimal("10"),
            status=status, cumulative_quantity=Decimal(filled),
            cumulative_average_price=(
                Decimal("88.5") if filled != "0" else None),
            cumulative_fees=Decimal("0"), updated_at=_AT))
    positions = [
        BrokerPositionSnapshot(symbol="SPY", quantity=Decimal("2")),
        BrokerPositionSnapshot(symbol="SQQQ", quantity=Decimal("-3"))]
    if held != "0":
        positions.append(BrokerPositionSnapshot(
            symbol="TQQQ", quantity=Decimal(held)))
    result = StartupReconciler(
        lifecycle_store=store, event_applier=service.apply_broker_event,
        cid_prefix=prefix,
    ).reconcile(AuthoritativeBrokerSnapshot(
        account_id=order.account_id, instance_id=order.instance_id,
        observed_at=observed, positions=tuple(positions),
        orders=tuple(orders), broker_available=True, positions_stable=True,
        orders_complete=True))
    record = store.require(order.idempotency_key)
    return [side.value, local, status, held, prefix, observed.isoformat(),
            sorted(result.owned.items()), sorted(result.external.items()),
            sorted(result.unresolved.items()), result.healthy,
            list(result.issues), result.evidence_hash, record.state.value,
            record.version, record.cumulative_quantity]


def _eb_cases(statuses=_EB_STATUSES, filled=None):
    for side in (OrderSide.BUY, OrderSide.SELL):
        for local in _EB_LOCAL:
            for status in statuses:
                for held in ("0", "4", "10", "12"):
                    for prefix in ("alpacama-", ""):
                        for observed in (_AT + timedelta(seconds=5),
                                         _AT + timedelta(minutes=5)):
                            yield (side, local, status,
                                   filled or _EB_FILLED.get(status, "0"),
                                   held, prefix, observed)


def _eb_reconcile_rows(statuses=_EB_STATUSES):
    return [_eb_case(*case) for case in _eb_cases(statuses)]


EB_RECONCILE_GRID_SHA256 = (
    "2df59c7f3c11859d269af61c5ff8f226e8159254929f966f70df5615e76b3616")


def test_eb_reconcile_grid_is_byte_identical():
    assert _digest(_eb_reconcile_rows()) == EB_RECONCILE_GRID_SHA256


# --- transport extras and multiplier cash (brief) -----------------------------

def _legs_lookup(*legs, fail=None):
    state = {"fail": fail}

    def lookup(order_id):
        if state["fail"]:
            raise RuntimeError(state["fail"])
        return SimpleNamespace(broker_order_id=order_id, legs=tuple(legs))

    return state, lookup


def _leg_event(order, cid, state, cumulative="0", average=None):
    return BrokerOrderEvent(
        event_id=f"{cid}:{state.value}:{cumulative}",
        account_id=order.account_id, instance_id=order.instance_id,
        client_order_id=cid, broker_order_id=f"broker-{cid}",
        symbol=order.symbol, side=OrderSide.SELL, state=state,
        cumulative_quantity=Decimal(cumulative),
        cumulative_average_price=Decimal(average) if average else None,
        cumulative_fees=Decimal("0"), occurred_at=RTH)


def _broker_order(cid, broker_id, *, symbol="AAPL", side="buy", qty="5",
                  status, filled="0", avg=None):
    return BrokerOrderSnapshot(
        client_order_id=cid, broker_order_id=broker_id, symbol=symbol,
        side=OrderSide(side), requested_quantity=Decimal(qty), status=status,
        cumulative_quantity=Decimal(filled),
        cumulative_average_price=Decimal(avg) if avg else None,
        cumulative_fees=Decimal("0"), updated_at=RTH)


def _snapshot(order, positions, orders):
    return AuthoritativeBrokerSnapshot(
        account_id=order.account_id, instance_id=order.instance_id,
        observed_at=RTH,
        positions=tuple(BrokerPositionSnapshot(symbol=s, quantity=Decimal(q))
                        for s, q in positions),
        orders=tuple(orders), broker_available=True, positions_stable=True,
        orders_complete=True)


def _reconcile(service, snap, prefix="instance-"):
    return StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
        cid_prefix=prefix).reconcile(snap)


def test_bracket_kwargs_carry_the_leg_prices_and_nothing_else():
    order = bracket_intent()
    calls, transport = _recording(order)
    assert _service(order, transport=transport,
                    provider=equity_snapshot).submit(order).accepted
    extras = {k: v for k, v in calls[0].items() if k not in EB_BUY_KWARGS}
    assert extras == {"order_class": "bracket", "take_profit": 109.0,
                      "stop_loss": 94.0}
    assert (calls[0]["tif"], calls[0]["qty"], calls[0]["order_type"]) == (
        "gtc", 5.0, "market")


def test_option_kwargs_and_contract_multiplier_cash():
    order = option_intent()
    calls, transport = _recording(order)
    fills = []
    service = _service(order, transport=transport, provider=option_snapshot,
                       confirmed_fill_handler=fills.append)
    assert service.submit(order).accepted
    assert calls[0]["asset_class"] == "us_option"
    assert calls[0]["position_intent"] == "sell_to_open"
    assert calls[0]["qty"] == 1.0 and "order_class" not in calls[0]
    assert service.reservation_for(order.idempotency_key).remaining_notional \
        == Decimal("120")
    applied = service.apply_broker_event(event(
        order, state=LifecycleState.FILLED, cumulative=Decimal("1"),
        average=Decimal("1.23"), fees=Decimal("0.65")))
    assert applied.fill.cash_delta == Decimal("122.35")
    assert applied.fill.position_delta == Decimal("-1")
    assert (applied.fill.asset_class, applied.fill.contract_multiplier) == (
        "us_option", 100)
    assert fills == [applied.fill]


def test_a_buy_to_close_debits_cash_times_the_multiplier():
    order = buy_to_close()
    calls, transport = _recording(order)
    service = _service(order, transport=transport,
                       provider=lambda i: option_snapshot(
                           i, position_quantity=Decimal("-1"),
                           quote_price=Decimal("0.40")))
    assert service.submit(order).accepted
    assert calls[0]["position_intent"] == "buy_to_close"
    assert "asset_class" in calls[0] and "order_class" not in calls[0]
    reservation = service.reservation_for(order.idempotency_key)
    assert reservation.remaining_notional == Decimal("40")
    applied = service.apply_broker_event(event(
        order, state=LifecycleState.FILLED, cumulative=Decimal("1"),
        average=Decimal("0.45"), fees=Decimal("0.65")))
    assert applied.fill.cash_delta == Decimal("-45.65")
    assert applied.fill.position_delta == Decimal("1")
    # 45.65 consumed against 40 reserved: a real overrun, flagged.
    assert order.idempotency_key in service.capacity_breaches


def test_a_restart_restores_option_reservations_times_the_multiplier():
    """_restore_reservations uses the same x100 as submit, so a restarted
    buy-to-close is not reported as a capacity breach by 100x."""
    order = buy_to_close(reference_price=Decimal("0.40"))
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    _service(order, transport=_recording(order)[1], store=store,
             provider=lambda i: option_snapshot(
                 i, position_quantity=Decimal("-1"),
                 quote_price=Decimal("0.40"))).submit(order)
    restarted = _service(order, store=store)
    assert restarted.reservation_for(order.idempotency_key).remaining_notional \
        == Decimal("40")
    restarted.apply_broker_event(event(
        order, state=LifecycleState.FILLED, cumulative=Decimal("1"),
        average=Decimal("0.39")))
    assert restarted.capacity_breaches == set()


# --- bracket legs (brief) -----------------------------------------------------

def _bracket_service(order, *, transport, lookup, fills=None, store=None):
    return _service(order, transport=transport, provider=equity_snapshot,
                    legs_lookup=lookup, store=store,
                    confirmed_fill_handler=(fills.append if fills is not None
                                            else None))


def test_a_bracket_submit_registers_both_legs_as_lifecycle_rows():
    order = bracket_intent()
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit", status="new"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(order, transport=lambda **kw: parent_ref(order),
                               lookup=lookup)
    assert service.submit(order).accepted
    store = service.lifecycle_store
    tp, sl = store.require("leg-tp"), store.require("leg-sl")
    for leg in (tp, sl):
        assert leg.intent.source is OrderSource.BRACKET_LEG
        assert leg.intent.parent_client_order_id == order.idempotency_key
        assert (leg.intent.side, leg.intent.reduce_only) == (OrderSide.SELL, True)
        assert leg.state is LifecycleState.ACKNOWLEDGED
    assert (tp.intent.order_type, tp.intent.limit_price) == ("limit", Decimal("109.0"))
    assert (sl.intent.order_type, sl.intent.stop_loss_price) == ("market", Decimal("94"))
    assert tp.broker_order_id == "broker-leg-tp"
    assert service.ensure_bracket_legs() == 0


def test_a_leg_fill_resolves_to_its_row_and_credits_the_sale():
    order = bracket_intent()
    fills = []
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit", status="new"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="filled", filled_qty="5", filled_avg_price="100"),
        lookup=lookup, fills=fills)
    service.submit(order)
    applied = service.apply_broker_event(
        _leg_event(order, "leg-tp", LifecycleState.FILLED, "5", "109"))
    assert applied.applied is True
    assert applied.fill.position_delta == Decimal("-5")
    assert applied.fill.cash_delta == Decimal("545")
    assert [f.event.side.value for f in fills] == ["buy", "sell"]


def test_leg_fill_before_registration_is_recovered_exactly_once():
    """Review Focus 1."""
    order = bracket_intent()
    fills = []
    state, lookup = _legs_lookup(
        leg_ref("leg-tp", "limit", status="filled", filled_qty="5",
                filled_avg_price="109"),
        leg_ref("leg-sl", "stop", status="canceled"),
        fail="nested read timed out")
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="filled", filled_qty="5", filled_avg_price="100"),
        lookup=lookup, fills=fills)
    assert service.submit(order).accepted is True
    with pytest.raises(LifecycleConflict):
        service.apply_broker_event(
            _leg_event(order, "leg-tp", LifecycleState.FILLED, "5", "109"))
    state["fail"] = None
    assert service.ensure_bracket_legs() == 2
    assert service.lifecycle_store.require("leg-tp").state is LifecycleState.FILLED
    parent = _broker_order(order.idempotency_key, "broker-parent",
                           status="filled", filled="5", avg="100")
    tp = _broker_order("leg-tp", "broker-leg-tp", side="sell",
                       status="filled", filled="5", avg="109")
    sl = _broker_order("leg-sl", "broker-leg-sl", side="sell",
                       status="canceled")
    result = _reconcile(service, _snapshot(order, [], [parent, tp, sl]))
    assert result.healthy is True and result.issues == ()
    assert [f.event.side.value for f in fills] == ["buy", "sell"]
    assert service.ensure_bracket_legs() == 0


def test_partial_parent_owns_only_filled_shares():
    """Review Focus 2."""
    order = bracket_intent()
    fills = []
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="partially_filled", filled_qty="3",
            filled_avg_price="100"),
        lookup=lookup, fills=fills)
    assert service.submit(order).accepted
    parent = _broker_order(order.idempotency_key, "broker-parent",
                           status="partially_filled", filled="3", avg="100")
    working = [
        _broker_order("leg-tp", "broker-leg-tp", side="sell", status="new"),
        _broker_order("leg-sl", "broker-leg-sl", side="sell", status="held"),
    ]
    result = _reconcile(service, _snapshot(order, [("AAPL", "3")],
                                           [parent, *working]))
    assert result.healthy is True, result.issues
    assert dict(result.owned) == {"AAPL": Decimal("3")}
    service.apply_broker_event(
        _leg_event(order, "leg-tp", LifecycleState.FILLED, "3", "109"))
    done = [
        _broker_order("leg-tp", "broker-leg-tp", side="sell", status="filled",
                      filled="3", avg="109"),
        _broker_order("leg-sl", "broker-leg-sl", side="sell",
                      status="canceled"),
    ]
    result = _reconcile(service, _snapshot(order, [], [parent, *done]))
    assert result.healthy is True, result.issues
    assert [f.event.side.value for f in fills] == ["buy", "sell"]


def test_held_and_pending_cancel_legs_are_working_orders_not_issues():
    order = bracket_intent()
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit", status="new"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="filled", filled_qty="5", filled_avg_price="100"),
        lookup=lookup)
    service.submit(order)
    orders = [
        _broker_order(order.idempotency_key, "broker-parent", status="filled",
                      filled="5", avg="100"),
        _broker_order("leg-tp", "broker-leg-tp", side="sell",
                      status="pending_cancel"),
        _broker_order("leg-sl", "broker-leg-sl", side="sell", status="held"),
    ]
    result = _reconcile(service, _snapshot(order, [("AAPL", "5")], orders))
    assert result.healthy is True and result.issues == ()
    assert dict(result.owned) == {"AAPL": Decimal("5")}


def test_a_leg_without_a_client_order_id_cannot_be_tracked():
    with pytest.raises(ValueError):
        bracket_leg_intent(bracket_intent(), leg_ref("", "limit"))


def test_a_failed_leg_read_never_refuses_the_accepted_parent():
    order = bracket_intent()
    logs = []
    _state, lookup = _legs_lookup(fail="nested read timed out")
    service = _service(order, transport=lambda **kw: parent_ref(order),
                       provider=equity_snapshot, legs_lookup=lookup,
                       log=lambda *a: logs.append(a))
    submission = service.submit(order)
    assert submission.accepted is True
    assert any("bracket.legs.unregistered" in line[0] for line in logs)


def test_legs_are_read_only_for_a_bracket_and_only_with_a_lookup():
    calls = []

    def lookup(order_id):
        calls.append(order_id)
        return SimpleNamespace(legs=())

    plain = intent()
    _service(plain, transport=_recording(plain)[1], provider=snapshot,
             legs_lookup=lookup).submit(plain)
    assert calls == []
    order = bracket_intent()
    assert _service(order, transport=lambda **kw: parent_ref(order),
                    provider=equity_snapshot).submit(order).accepted
    assert _service(order, transport=lambda **kw: parent_ref(order),
                    provider=equity_snapshot).ensure_bracket_legs() == 0


# --- external fills (assignments) (brief) -------------------------------------

def _assignment(**changes):
    values = dict(
        account_id="acct-1", instance_id="instance-1",
        source=OrderSource.OPTION_ACTIVITY, reason=f"wheel_assignment:{OCC}",
        symbol="APH", side=OrderSide.BUY, quantity=Decimal("100"),
        reduce_only=False, decision_at=RTH, quote_at=RTH,
        risk_snapshot_id="option-activity", reference_price=Decimal("130"),
        broker_client_order_id="opasn-abc")
    values.update(changes)
    return OrderIntent(**values)


def test_record_external_fill_is_exactly_once():
    fills = []
    assignment = _assignment()
    service = _service(assignment, confirmed_fill_handler=fills.append)
    kwargs = dict(broker_order_id="opasn-abc", quantity=Decimal("100"),
                  price=Decimal("130"), occurred_at=RTH,
                  reason="option assignment")
    first = service.record_external_fill(assignment, **kwargs)
    again = service.record_external_fill(assignment, **kwargs)
    assert first.applied is True and first.fill.cash_delta == Decimal("-13000")
    assert again.applied is False and len(fills) == 1


# --- reconcile: short options (brief) -----------------------------------------

def _short_put_service(*, filled=True):
    order = option_intent()
    service = _service(order, transport=lambda **kw: broker_ref(order),
                       provider=option_snapshot)
    assert service.submit(order).accepted
    if filled:
        service.apply_broker_event(event(
            order, state=LifecycleState.FILLED, cumulative=Decimal("1"),
            average=Decimal("1.20")))
    return order, service


def _sto_row(order):
    return _broker_order(order.idempotency_key, "broker-1", symbol=OCC,
                         side="sell", qty="1", status="filled", filled="1",
                         avg="1.20")


def test_a_short_option_with_lifecycle_lineage_is_owned():
    order, service = _short_put_service()
    result = _reconcile(service, _snapshot(order, [(OCC, "-1")],
                                           [_sto_row(order)]))
    assert dict(result.owned) == {OCC: Decimal("-1")}
    assert dict(result.external) == {}
    assert result.healthy is True and result.issues == ()


def test_a_short_without_option_lineage_stays_external():
    order = option_intent()
    service = _service(order)
    result = _reconcile(service, _snapshot(
        order, [(OCC, "-1"), ("AAPL", "-3")], []))
    assert dict(result.external) == {OCC: Decimal("-1"), "AAPL": Decimal("-3")}
    assert dict(result.owned) == {} and result.healthy is True


def test_an_expired_or_assigned_short_put_leaves_no_issue():
    order, service = _short_put_service()
    result = _reconcile(service, _snapshot(order, [], [_sto_row(order)]))
    assert result.healthy is True and result.issues == ()


# --- ruling 7: ownership edges ------------------------------------------------

def test_a_broker_short_larger_than_our_lineage_splits_owned_and_external():
    order, service = _short_put_service()
    result = _reconcile(service, _snapshot(order, [(OCC, "-3")],
                                           [_sto_row(order)]))
    assert dict(result.owned) == {OCC: Decimal("-1")}
    assert dict(result.external) == {OCC: Decimal("-2")}
    assert result.healthy is True and result.issues == ()


def test_a_closed_short_with_the_broker_still_short_is_external():
    """Sold and bought back: lineage nets to zero, so a -1 the broker still
    shows is not ours."""
    order, service = _short_put_service()
    close = buy_to_close()
    service = _service(close, store=service.lifecycle_store,
                       transport=lambda **kw: broker_ref(
                           close, broker_order_id="broker-2"),
                       provider=lambda i: option_snapshot(
                           i, position_quantity=Decimal("-1")))
    assert service.submit(close).accepted
    service.apply_broker_event(event(
        close, state=LifecycleState.FILLED, cumulative=Decimal("1"),
        average=Decimal("0.40"), broker_order_id="broker-2"))
    result = _reconcile(service, _snapshot(order, [(OCC, "-1")], [
        _sto_row(order),
        _broker_order(close.idempotency_key, "broker-2", symbol=OCC,
                      side="buy", qty="1", status="filled", filled="1",
                      avg="0.40")]))
    assert dict(result.owned) == {} and dict(result.external) == {
        OCC: Decimal("-1")}


def test_a_negative_equity_quantity_is_never_owned_even_with_lineage():
    """Negative broker quantities are owned only for us_option: an EB-style
    sell lineage on a symbol the broker shows short stays external."""
    exit_ = intent(side=OrderSide.SELL, reduce_only=True,
                   source=OrderSource.RISK_EXIT, quantity=Decimal("3"))
    service = _service(exit_, transport=lambda **kw: broker_ref(exit_),
                       provider=snapshot)
    assert service.submit(exit_).accepted
    service.apply_broker_event(event(
        exit_, state=LifecycleState.FILLED, cumulative=Decimal("3"),
        average=Decimal("100")))
    result = _reconcile(service, _snapshot(exit_, [("AAPL", "-3")], [
        _broker_order(exit_.idempotency_key, "broker-1", side="sell",
                      qty="3", status="filled", filled="3", avg="100")]))
    assert dict(result.owned) == {}
    assert dict(result.external) == {"AAPL": Decimal("-3")}


@pytest.mark.parametrize("status", ["pending_cancel", "held"])
def test_the_new_statuses_are_strictly_less_blocking_for_eb(status):
    """Pre-flight ruling. Before this change held/pending_cancel were
    unsupported: the order was skipped with an unsupported_broker_status
    issue, exactly how "replaced" is still handled, so the reconcile was
    always unhealthy. Over the whole EB grid, at every cumulative the broker
    could report, the new status never reports unsupported_broker_status,
    and the only issues it can add are the lineage checks that compare the
    broker's own reported fill with its own positions. With no new fill it
    adds none at all."""
    compared = healthy = 0
    lineage = ("broker_quantity_below_lineage:",
               "lineage_position_missing_broker:")
    for filled in ("0", "4", "6", "10"):
        for case in _eb_cases((status,), filled=filled):
            new = _eb_case(*case)
            old = _eb_case(*(case[:2] + ("replaced",) + case[3:]))
            assert old[9] is False and any(
                i.startswith("unsupported_broker_status") for i in old[10])
            assert not any(i.startswith("unsupported_broker_status")
                           for i in new[10])
            extra = set(new[10]) - set(old[10])
            assert all(i.startswith(lineage) for i in extra), (case, extra)
            if filled == "0":
                assert not extra, (case, extra)
            compared += 1
            healthy += new[9]
    assert compared == 4 * 224 and healthy > 0


def test_pending_cancel_defers_a_new_fill_to_the_terminal_status():
    """A PARTIAL order that fills more while cancelling: the ACK-mapped
    pending_cancel cannot move PARTIAL back, so the extra fill waits for the
    terminal canceled status and is then counted exactly once."""
    order, store = _eb_book("partial", OrderSide.BUY)
    fills = []
    service = _service(order, store=store, confirmed_fill_handler=fills.append)

    def rows(status, filled):
        return AuthoritativeBrokerSnapshot(
            account_id=order.account_id, instance_id=order.instance_id,
            observed_at=_AT, positions=(BrokerPositionSnapshot(
                symbol="TQQQ", quantity=Decimal(filled)),),
            orders=(BrokerOrderSnapshot(
                client_order_id=order.idempotency_key, broker_order_id="b-1",
                symbol="TQQQ", side=OrderSide.BUY,
                requested_quantity=Decimal("10"), status=status,
                cumulative_quantity=Decimal(filled),
                cumulative_average_price=Decimal("88"),
                cumulative_fees=Decimal("0"), updated_at=_AT),),
            broker_available=True, positions_stable=True,
            orders_complete=True)

    result = _reconcile(service, rows("pending_cancel", "6"), "alpacama-")
    assert result.healthy is True and fills == []
    assert store.require(order.idempotency_key).cumulative_quantity == 4
    result = _reconcile(service, rows("canceled", "6"), "alpacama-")
    assert result.healthy is True
    assert [f.incremental_quantity for f in fills] == [Decimal("2")]
    result = _reconcile(service, rows("canceled", "6"), "alpacama-")
    assert len(fills) == 1


# --- ruling 5: record-only sources are never submitted ------------------------

@pytest.mark.parametrize("method", ["submit", "enqueue", "submit_intent"])
@pytest.mark.parametrize("build", ["leg", "assignment"])
def test_record_only_sources_are_never_submitted(method, build):
    order = (bracket_leg_intent(bracket_intent(), leg_ref("leg-tp", "limit"))
             if build == "leg" else _assignment())
    calls, transport = _recording(order)
    provided = []
    service = _service(order, transport=transport,
                       provider=lambda i: provided.append(i) or snapshot(i))
    submission = getattr(service, method)(order)
    assert submission.decision.reason_codes == (
        "authorization.record_only_source",)
    assert submission.accepted is False and submission.uncertain is False
    assert calls == [] and provided == []
    assert service.lifecycle_store.get(order.idempotency_key) is None


# --- ruling 4: broker-keyed rows are ours, and retries are not drift ----------

@pytest.mark.parametrize("instance_id", ["alpaca-main", "swing-paper",
                                         "strategy-eb-lab", "instance-1",
                                         "a", "--"])
def test_a_leg_row_links_to_a_parent_carrying_this_instances_prefix(
        instance_id):
    """The broker mints a leg's client order id, so the key carries no
    instance prefix. The row is linked, by construction, to a parent whose
    key carries exactly the prefix the classifier (derive_cid_prefix) and the
    reconciler (broker.py's _cid_safe) recognise as ours."""
    from broker_adapters._classifier import derive_cid_prefix
    from broker_adapters._client_order_id import _safe

    from live_orders.types import instance_key_prefix

    prefix = derive_cid_prefix(instance_id)
    assert prefix == f"{_safe(instance_id, 8) or 'x'}-"
    assert prefix == instance_key_prefix(instance_id)
    parent = bracket_intent(instance_id=instance_id)
    assert parent.idempotency_key.startswith(prefix)
    leg = bracket_leg_intent(parent, leg_ref("0f5c3b1e-2d7a-4a55", "limit"))
    assert leg.idempotency_key == "0f5c3b1e-2d7a-4a55"
    assert not leg.idempotency_key.startswith(prefix)
    assert leg.parent_client_order_id.startswith(prefix)


@pytest.mark.parametrize("changes,match", [
    ({"parent_client_order_id": None}, "parent"),
    ({"parent_client_order_id": "someoneel-abc-0"}, "parent"),
    ({"broker_client_order_id": None}, "client order id"),
    ({"reduce_only": False}, "reduce-only"),
])
def test_a_leg_row_must_be_linked_to_one_of_our_parents(changes, match):
    leg = bracket_leg_intent(bracket_intent(), leg_ref("leg-tp", "limit"))
    with pytest.raises(ValueError, match=match):
        replace(leg, **changes)


def test_legs_with_broker_minted_ids_reconcile_under_the_production_prefix():
    """alpaca-main-style prefix, UUID leg ids: each leg resolves to its own
    registered row, its fill nets the parent's lineage, and nothing is
    external or an issue."""
    from broker_adapters._classifier import derive_cid_prefix

    order = bracket_intent(account_id="brk-paper", instance_id="swing-paper")
    tp_cid, sl_cid = "5b1d9e0c-1f7e-4c0b-9a1e-tp", "5b1d9e0c-1f7e-4c0b-9a1e-sl"
    fills = []
    _state, lookup = _legs_lookup(
        leg_ref(tp_cid, "limit", status="new"), leg_ref(sl_cid, "stop"))
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="filled", filled_qty="5", filled_avg_price="100"),
        lookup=lookup, fills=fills)
    assert service.submit(order).accepted
    service.apply_broker_event(
        _leg_event(order, tp_cid, LifecycleState.FILLED, "5", "109"))
    result = _reconcile(service, _snapshot(order, [], [
        _broker_order(order.idempotency_key, "broker-parent",
                      status="filled", filled="5", avg="100"),
        _broker_order(tp_cid, f"broker-{tp_cid}", side="sell",
                      status="filled", filled="5", avg="109"),
        _broker_order(sl_cid, f"broker-{sl_cid}", side="sell",
                      status="canceled"),
    ]), prefix=derive_cid_prefix("swing-paper"))
    assert result.healthy is True and result.issues == ()
    assert dict(result.owned) == {} and dict(result.external) == {}
    assert service.lifecycle_store.require(sl_cid).state is \
        LifecycleState.CANCELED
    assert [f.event.side.value for f in fills] == ["buy", "sell"]


def test_an_orphan_leg_row_is_not_proof_of_ownership():
    """Explicit parent linkage: a leg row whose parent is not in this
    instance's lifecycle is not ours. Its broker order is treated like any
    foreign order (informational under a prefix), its row is left alone, and
    it can never pin the reconcile unhealthy."""
    parent = bracket_intent()            # never recorded
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    orphan = bracket_leg_intent(parent, leg_ref("leg-orphan", "limit"))
    store.create_intent(orphan)
    working = bracket_leg_intent(parent, leg_ref("leg-gone", "stop"))
    store.create_intent(working)
    service = _service(parent, store=store)
    for leg in (orphan, working):
        service.apply_broker_event(BrokerOrderEvent(
            event_id=f"{leg.idempotency_key}:ack",
            account_id=leg.account_id, instance_id=leg.instance_id,
            client_order_id=leg.idempotency_key,
            broker_order_id=f"broker-{leg.idempotency_key}", symbol="AAPL",
            side=OrderSide.SELL, state=LifecycleState.ACKNOWLEDGED,
            cumulative_quantity=Decimal("0"), cumulative_average_price=None,
            cumulative_fees=Decimal("0"), occurred_at=RTH))
    result = _reconcile(service, _snapshot(parent, [("AAPL", "5")], [
        _broker_order("leg-orphan", "broker-leg-orphan", side="sell",
                      status="filled", filled="5", avg="109")]))
    assert result.healthy is True and result.issues == ()
    assert store.require("leg-orphan").state is LifecycleState.ACKNOWLEDGED
    assert dict(result.external) == {"AAPL": Decimal("5")}


def test_a_retried_broker_keyed_intent_is_refused_never_identity_drift():
    order = bracket_intent()
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit", status="new"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(order, transport=lambda **kw: parent_ref(order),
                               lookup=lookup)
    assert service.submit(order).accepted
    leg = service.lifecycle_store.require("leg-tp").intent
    retry = replace(leg, retry_ordinal=1)
    assert retry.idempotency_key == "leg-tp"      # the broker's id, reused
    assert not retry.same_identity(leg)           # what drift would report
    submission = service.submit(retry)
    assert submission.decision.reason_codes == (
        "authorization.record_only_source",)
    for keyed in (leg, _assignment()):
        with pytest.raises(TerminalRetryExhausted, match="broker"):
            new_retry_intent(keyed, reason="retry", maximum=5)
    # EB's own retries are unchanged.
    assert new_retry_intent(intent(), reason="r", maximum=2).retry_ordinal == 1


def test_registering_legs_again_is_idempotent_and_never_drift():
    order = bracket_intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    legs = [leg_ref("leg-tp", "limit", status="new"),
            leg_ref("leg-sl", "stop")]
    _state, lookup = _legs_lookup(*legs)
    service = _bracket_service(order, transport=lambda **kw: parent_ref(order),
                               lookup=lookup, store=store)
    assert service.submit(order).accepted
    first = {r.client_order_id: r.version
             for r in store.list_for_instance(order.instance_id)}
    # The broker now reports a different leg quantity; re-registration keeps
    # the row it has instead of raising identity drift.
    legs[0] = leg_ref("leg-tp", "limit", status="new", qty="3")
    _state, lookup = _legs_lookup(*legs)
    again = _bracket_service(order, transport=None, lookup=lookup, store=store)
    rows = again.register_bracket_legs(order, parent_ref(order))
    assert [r.client_order_id for r in rows] == ["leg-tp", "leg-sl"]
    assert store.require("leg-tp").intent.quantity == Decimal("5")
    assert again.ensure_bracket_legs() == 0
    assert {r.client_order_id: r.version
            for r in store.list_for_instance(order.instance_id)} == first


# --- L2 review: contracts never enter the equity quarantine -------------------

def _adapter_with_a_short_put(*, lineage):
    from swing_alpaca_fakes import (
        FakeOptionsTradingClient, contract_row, enum, make_adapter,
        option_position)

    equity = SimpleNamespace(symbol="TQQQ", qty="10", market_value="880",
                             avg_entry_price="80",
                             asset_class=enum("us_equity"))
    client = FakeOptionsTradingClient(
        positions=[equity, option_position(OCC)],
        contracts_by_symbol={OCC: contract_row(OCC)})
    adapter = make_adapter(client, instance_id="instance-1", clean_room=True)
    if not lineage:
        return adapter, _service(option_intent())
    order, service = _short_put_service()
    client.orders = {"broker-1": SimpleNamespace(
        id="broker-1", client_order_id=order.idempotency_key, symbol=OCC,
        side=enum("sell"), qty="1", status=enum("filled"), filled_qty="1",
        filled_avg_price="1.20", filled_fees="0", order_class=enum("simple"),
        position_intent=enum("sell_to_open"), asset_class=enum("us_option"),
        submitted_at=RTH, updated_at=RTH)}
    return adapter, service


@pytest.mark.parametrize("lineage", [False, True])
def test_option_contracts_never_enter_the_equity_quarantine(lineage):
    """broker.py's boot audit prints every _external_positions row as
    "<symbol>=<qty>sh external"; a contract there read "-1.0000sh". Contracts
    live in _option_positions only: never in the startup quarantine, the
    pending broker view, or the equity mirrors a reconcile publishes."""
    adapter, service = _adapter_with_a_short_put(lineage=lineage)
    assert [row["symbol"] for row in adapter._pending_broker_positions] == [
        "TQQQ"]
    assert set(adapter._external_positions) == {"TQQQ"}
    assert adapter._option_positions[OCC].qty == -1
    snap = adapter.capture_reconciliation_snapshot(account_id="acct-1")
    result = StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
        cid_prefix="instance-").reconcile(snap)
    # The reconcile itself still reports the truth about the contract.
    expected_owned = {OCC: Decimal("-1")} if lineage else {}
    assert dict(result.owned) == expected_owned
    assert (OCC in result.external) is (not lineage)
    adapter.complete_startup_reconciliation(result)
    assert set(adapter._external_positions) == {"TQQQ"}
    assert all(row["qty"] > 0 for row in adapter._external_positions.values())
    assert OCC not in adapter._positions
    assert OCC not in adapter._unresolved_positions
    assert all(trade["ticker"] != OCC for trade in adapter._trades)
    assert adapter._option_positions[OCC].qty == -1


def test_an_expired_contract_stays_out_of_the_equity_trade_history():
    """The contract is gone from the account, so only the lifecycle's own
    option lineage (ReconciliationResult.option_symbols) can tell the
    adapter that its sell-to-open event is not an equity trade."""
    adapter, service = _adapter_with_a_short_put(lineage=True)
    adapter._client.positions = adapter._client.positions[:1]   # expired
    adapter.refresh_positions()
    assert adapter._option_positions == {}
    snap = adapter.capture_reconciliation_snapshot(account_id="acct-1")
    result = StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
        cid_prefix="instance-").reconcile(snap)
    assert result.option_symbols == frozenset({OCC})
    assert result.healthy is True and result.issues == ()
    adapter.complete_startup_reconciliation(result)
    assert [trade["ticker"] for trade in adapter._trades] == []


def test_a_contract_newer_than_the_last_refresh_is_still_kept_out():
    """A manual contract opened after the adapter's last positions refresh
    is recognised from the reconciliation snapshot's own asset class."""
    from swing_alpaca_fakes import option_position

    adapter, service = _adapter_with_a_short_put(lineage=False)
    adapter._client.positions = adapter._client.positions[:1]
    adapter.refresh_positions()
    assert adapter._option_positions == {}
    adapter._client.positions.append(option_position(OCC, qty="-2"))
    snap = adapter.capture_reconciliation_snapshot(account_id="acct-1")
    result = StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
        cid_prefix="instance-").reconcile(snap)
    assert dict(result.external)[OCC] == Decimal("-2")
    adapter.complete_startup_reconciliation(result)
    assert set(adapter._external_positions) == {"TQQQ"}


def test_eb_mirrors_after_a_reconcile_are_unchanged_by_the_contract_filter():
    """EB's account holds no contract: every set the filter reads is empty
    and the published mirrors are exactly the reconcile's."""
    from swing_alpaca_fakes import FakeTradingClient, enum, make_adapter

    rows = [SimpleNamespace(symbol=s, qty=q, market_value=mv,
                            avg_entry_price="1", asset_class=enum("us_equity"))
            for s, q, mv in (("TQQQ", "10", "880"), ("GLD", "4", "1200"))]
    adapter = make_adapter(FakeTradingClient(positions=rows),
                           instance_id="alpaca-main", clean_room=True)
    assert [r["symbol"] for r in adapter._pending_broker_positions] == [
        "TQQQ", "GLD"]
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    service = LiveOrderService(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        snapshot_provider=None, transport=None, lifecycle_store=store)
    result = StartupReconciler(
        lifecycle_store=store, event_applier=service.apply_broker_event,
        cid_prefix="alpacama-").reconcile(
            adapter.capture_reconciliation_snapshot(account_id="brk-alpaca-main"))
    assert result.option_symbols == frozenset()
    adapter.complete_startup_reconciliation(result)
    assert {k: v["qty"] for k, v in adapter._external_positions.items()} == {
        "TQQQ": 10.0, "GLD": 4.0}
    assert adapter._reconciliation_option_symbols == frozenset()
