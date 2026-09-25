"""L3 review rulings M1 and M3, routed to L4 (A-live ledger 2026-09-25).

M1: LiveOrderService.record_external_fill wrote ANY intent FILLED with a
ConfirmedFill and no broker order. An EB-shaped STRATEGY intent recorded that
way booked a position no order produced, and its real submit was then refused
idempotency.terminal_requires_retry. Only option activity with the broker's
activity id may be recorded.

M3: DependencySnapshot.pending_sell_to_open_collateral defaulted to 0 while
its sibling open_short_put_collateral defaulted to None, so a snapshot that
omitted it failed OPEN. Unknown now fails closed.
"""
import dataclasses
import itertools
from decimal import Decimal

import pytest

from live_order_task8_helpers import broker_ref, intent, snapshot
from live_orders import (
    DependencySnapshot,
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderIntent,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
    UnifiedOrderGate,
)
from swing_live_fixtures import (
    OCC,
    RTH,
    buy_to_close,
    option_intent,
    option_snapshot,
)


def _service(order, **kwargs):
    return LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=kwargs.pop("provider", snapshot),
        transport=kwargs.pop("transport", lambda **kw: broker_ref(order)),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        **kwargs)


FILL = dict(broker_order_id="opasn-abc", quantity=Decimal("100"),
            price=Decimal("130"), occurred_at=RTH, reason="option assignment")


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


# --- M1: external fills only from option activity -----------------------------

@pytest.mark.parametrize("order", [
    intent(symbol="TQQQ", reason="eb_rebalance"),
    intent(symbol="GLD", reason="eb_rotation_trim", side=OrderSide.SELL,
           reduce_only=True, source=OrderSource.RISK_EXIT),
    intent(symbol="SPY", reason="manual", source=OrderSource.MANUAL),
], ids=["strategy", "risk_exit", "manual"])
def test_an_order_intent_cannot_be_recorded_as_an_external_fill(order):
    fills = []
    service = _service(order, confirmed_fill_handler=fills.append)
    with pytest.raises(ValueError, match="option activity only"):
        service.record_external_fill(order, **dict(FILL, broker_order_id="b-1"))
    assert service.lifecycle_store.get(order.idempotency_key) is None
    assert fills == []
    # The reviewer's sequence: the real submit is NOT refused
    # idempotency.terminal_requires_retry afterwards.
    submission = service.submit(order)
    assert submission.accepted, submission.decision.reason_codes


@pytest.mark.parametrize("broker_id", ["", "   ", None])
def test_option_activity_needs_the_brokers_activity_id(broker_id):
    assignment = _assignment()
    fills = []
    service = _service(assignment, confirmed_fill_handler=fills.append)
    with pytest.raises(ValueError, match="activity id"):
        service.record_external_fill(
            assignment, **dict(FILL, broker_order_id=broker_id))
    assert service.lifecycle_store.get(assignment.idempotency_key) is None
    assert fills == []


def test_option_activity_with_a_broker_id_is_recorded_exactly_once():
    assignment = _assignment()
    fills = []
    service = _service(assignment, confirmed_fill_handler=fills.append)
    first = service.record_external_fill(assignment, **FILL)
    again = service.record_external_fill(assignment, **FILL)
    assert first.applied is True and first.fill.cash_delta == Decimal("-13000")
    assert again.applied is False and len(fills) == 1


# --- M3: unknown pending sell-to-open collateral fails closed -----------------

def _omitting(snap, name):
    """The same snapshot, built by a provider that never passed ``name``."""
    values = {field.name: getattr(snap, field.name)
              for field in dataclasses.fields(snap)
              if field.init and field.name != name}
    return DependencySnapshot(**values)


def test_an_omitted_pending_figure_is_unknown_not_zero():
    order = option_intent()
    omitted = _omitting(option_snapshot(order), "pending_sell_to_open_collateral")
    assert omitted.pending_sell_to_open_collateral is None
    assert omitted.open_short_put_collateral == Decimal("0")


def test_a_put_with_pending_collateral_unknown_is_refused():
    """The reviewer's case: 13,000 cash, 0 open, pending omitted, a 130
    strike put needing exactly 13,000. It was allowed."""
    order = option_intent()
    known = option_snapshot(order, available_cash=Decimal("13000"))
    assert UnifiedOrderGate().evaluate(order, known).allowed
    unknown = _omitting(known, "pending_sell_to_open_collateral")
    decision = UnifiedOrderGate().evaluate(order, unknown)
    assert decision.allowed is False
    assert "option.collateral_unknown" in decision.reason_codes
    assert "option.collateral_insufficient" not in decision.reason_codes


def test_an_explicit_none_is_refused_too():
    order = option_intent()
    decision = UnifiedOrderGate().evaluate(
        order, option_snapshot(order, pending_sell_to_open_collateral=None))
    assert "option.collateral_unknown" in decision.reason_codes


def test_a_negative_pending_figure_is_still_refused_at_construction():
    with pytest.raises(ValueError, match="pending_sell_to_open_collateral"):
        option_snapshot(option_intent(), pending_sell_to_open_collateral="-1")


def test_a_buy_to_close_needs_no_collateral_figure():
    order = buy_to_close()
    snap = _omitting(
        option_snapshot(order, position_quantity=Decimal("-1"),
                        quote_price=Decimal("1.20")),
        "pending_sell_to_open_collateral")
    decision = UnifiedOrderGate().evaluate(order, snap)
    assert decision.allowed, decision.reason_codes


# --- EB pin: equity snapshots never read the option-only field ----------------

_EB_INTENTS = (
    intent(symbol="TQQQ", reason="eb_rebalance"),
    intent(symbol="TQQQ", reason="eb_sweep", quantity=Decimal("0.5")),
    intent(symbol="GLD", reason="eb_rotation_trim", side=OrderSide.SELL,
           quantity=Decimal("3"), reduce_only=True),
    intent(symbol="GDX", reason="eb_exit", side=OrderSide.SELL,
           quantity=Decimal("2"), reduce_only=True, order_type="limit",
           limit_price=Decimal("99.5"), extended_hours=True,
           source=OrderSource.RISK_EXIT),
)
_EB_SNAPSHOT_CHANGES = (
    {}, {"available_cash": Decimal("1")}, {"market_open": False},
    {"position_quantity": Decimal("0")}, {"quote_price": Decimal("0")},
    {"max_order_notional": Decimal("10")}, {"armed": False},
    {"positions": "unhealthy"}, {"quote": "unknown"},
)


def test_equity_gate_decisions_ignore_the_pending_default():
    """Every EB-shaped decision is identical whether the snapshot carries the
    new default (None, unknown) or the old one (0)."""
    gate = UnifiedOrderGate()
    compared = 0
    for order, changes in itertools.product(_EB_INTENTS, _EB_SNAPSHOT_CHANGES):
        new_default = snapshot(order, **changes)
        assert new_default.pending_sell_to_open_collateral is None
        old_default = snapshot(order, pending_sell_to_open_collateral=Decimal("0"),
                               **changes)
        assert gate.evaluate(order, new_default) == gate.evaluate(order, old_default)
        compared += 1
    assert compared == len(_EB_INTENTS) * len(_EB_SNAPSHOT_CHANGES)
