from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from live_orders import (
    DependencySnapshot,
    Health,
    OrderIntent,
    OrderSide,
    OrderSource,
    UnifiedOrderGate,
)
from live_state import LiveOrderService


NOW = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)
ALL_SOURCES = frozenset(OrderSource)


def buy_intent(**changes):
    values = {
        "account_id": "acct-1",
        "instance_id": "instance-1",
        "source": OrderSource.STRATEGY,
        "reason": "allocation",
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "quantity": Decimal("2"),
        "reduce_only": False,
        "decision_at": NOW,
        "quote_at": NOW,
        "risk_snapshot_id": "risk-7",
    }
    values.update(changes)
    return OrderIntent(**values)


def sell_intent(**changes):
    values = {
        "side": OrderSide.SELL,
        "quantity": Decimal("12"),
        "reduce_only": True,
        "source": OrderSource.RISK_EXIT,
        "reason": "drawdown exit",
    }
    values.update(changes)
    return buy_intent(**values)


def healthy_snapshot(**changes):
    values = {
        "account_id": "acct-1",
        "instance_id": "instance-1",
        "observed_at": NOW,
        "armed": True,
        "kill_switch": Health.HEALTHY,
        "quote": Health.HEALTHY,
        "cash": Health.HEALTHY,
        "positions": Health.HEALTHY,
        "calendar": Health.HEALTHY,
        "persistence": Health.HEALTHY,
        "risk_state": Health.HEALTHY,
        "watchdog": Health.HEALTHY,
        "quote_symbol": "AAPL",
        "quote_price": Decimal("100"),
        "quote_at": NOW,
        "position_symbol": "AAPL",
        "position_quantity": Decimal("5"),
        "positions_at": NOW,
        "available_cash": Decimal("1000"),
        "market_open": True,
        "risk_snapshot_id": "risk-7",
        "kill_switch_at": NOW,
        "cash_at": NOW,
        "calendar_at": NOW,
        "persistence_at": NOW,
        "risk_state_at": NOW,
        "watchdog_at": NOW,
        "max_order_notional": Decimal("500"),
        "max_position_quantity": Decimal("10"),
        "open_order_idempotency_keys": frozenset(),
        "authorized_sources": ALL_SOURCES,
    }
    values.update(changes)
    return DependencySnapshot(**values)


@pytest.mark.parametrize(
    "field",
    [
        "kill_switch",
        "quote",
        "cash",
        "positions",
        "calendar",
        "persistence",
        "risk_state",
        "watchdog",
    ],
)
def test_unknown_dependency_blocks_new_exposure(field):
    snap = replace(healthy_snapshot(), **{field: Health.UNKNOWN})
    decision = UnifiedOrderGate().evaluate(buy_intent(), snap)

    assert decision.allowed is False
    assert f"dependency.{field}.unknown" in decision.reason_codes


def test_reduce_only_quantity_is_capped_to_fresh_position():
    decision = UnifiedOrderGate().evaluate(
        sell_intent(quantity=Decimal("12"), reduce_only=True),
        healthy_snapshot(position_quantity=Decimal("5")),
    )

    assert decision.allowed is True
    assert decision.approved_quantity == Decimal("5")
    assert "reduce_only.quantity_capped" in decision.reason_codes


def test_reduce_only_still_requires_matching_fresh_quote_and_position_identity():
    stale = NOW - timedelta(minutes=5)
    decision = UnifiedOrderGate().evaluate(
        sell_intent(),
        healthy_snapshot(
            quote_symbol="MSFT",
            positions_at=stale,
        ),
    )

    assert decision.allowed is False
    assert "quote.symbol_mismatch" in decision.reason_codes
    assert "positions.stale" in decision.reason_codes


def test_gate_returns_all_ordered_reason_codes_and_performs_no_io():
    decision = UnifiedOrderGate().evaluate(
        buy_intent(account_id="wrong", instance_id="wrong-instance"),
        healthy_snapshot(
            armed=False,
            quote=Health.UNKNOWN,
            cash=Health.UNKNOWN,
            market_open=False,
            available_cash=Decimal("1"),
            max_position_quantity=Decimal("1"),
            authorized_sources=frozenset(),
        ),
    )

    assert decision.allowed is False
    assert decision.reason_codes[:3] == (
        "identity.account_mismatch",
        "identity.instance_mismatch",
        "identity.not_armed",
    )
    assert "dependency.quote.unknown" in decision.reason_codes
    assert "market.closed" in decision.reason_codes
    assert "cash.insufficient" in decision.reason_codes
    assert "authorization.source_denied" in decision.reason_codes


def test_open_order_idempotency_key_blocks_duplicate():
    intent = buy_intent()
    decision = UnifiedOrderGate().evaluate(
        intent,
        healthy_snapshot(
            open_order_idempotency_keys=frozenset({intent.idempotency_key}),
        ),
    )

    assert decision.allowed is False
    assert "idempotency.open_order_exists" in decision.reason_codes


def test_service_calls_injected_transport_only_after_approval():
    intent = buy_intent()
    calls = []
    service = LiveOrderService(
        account_id="acct-1",
        instance_id="instance-1",
        snapshot_provider=lambda _intent: healthy_snapshot(),
        transport=lambda **kwargs: calls.append(kwargs) or object(),
    )

    submission = service.enqueue(intent)
    duplicate = service.enqueue(intent)

    assert submission.accepted is True
    assert calls[0]["qty"] == 2.0
    assert calls[0]["client_order_id"] == intent.idempotency_key
    assert duplicate.accepted is False
    assert duplicate.decision.reason_codes == ("idempotency.open_order_exists",)
    assert len(calls) == 1


def test_service_snapshot_failure_is_denied_without_transport():
    calls = []

    def unavailable(_intent):
        raise RuntimeError("dependency read failed")

    submission = LiveOrderService(
        account_id="acct-1",
        instance_id="instance-1",
        snapshot_provider=unavailable,
        transport=lambda **kwargs: calls.append(kwargs),
    ).enqueue(buy_intent())

    assert submission.accepted is False
    assert submission.decision.reason_codes == ("dependency.snapshot.unavailable",)
    assert calls == []
