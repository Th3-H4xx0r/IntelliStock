from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from live_orders import (
    DependencySnapshot,
    Health,
    OrderIntent,
    OrderSide,
    OrderSource,
    UnifiedOrderGate,
)


NOW = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)


def _intent(*, reduce_only=False):
    return OrderIntent(
        account_id="acct",
        instance_id="instance",
        source=OrderSource.RISK_EXIT if reduce_only else OrderSource.STRATEGY,
        reason="test",
        symbol="AAPL",
        side=OrderSide.SELL if reduce_only else OrderSide.BUY,
        quantity=Decimal("2"),
        reduce_only=reduce_only,
        decision_at=NOW,
        quote_at=NOW,
        risk_snapshot_id="risk",
        reference_price=Decimal("100"),
    )


def _snapshot(**changes):
    values = {
        "account_id": "acct",
        "instance_id": "instance",
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
        "risk_snapshot_id": "risk",
        "kill_switch_at": NOW,
        "cash_at": NOW,
        "calendar_at": NOW,
        "persistence_at": NOW,
        "risk_state_at": NOW,
        "watchdog_at": NOW,
    }
    values.update(changes)
    return DependencySnapshot(**values)


@pytest.mark.parametrize(
    ("name", "timestamp_field"),
    [
        ("kill_switch", "kill_switch_at"),
        ("cash", "cash_at"),
        ("calendar", "calendar_at"),
        ("persistence", "persistence_at"),
        ("risk_state", "risk_state_at"),
        ("watchdog", "watchdog_at"),
    ],
)
def test_stale_healthy_dependency_still_blocks_new_exposure(
    name, timestamp_field
):
    stale = NOW - timedelta(minutes=10)
    decision = UnifiedOrderGate().evaluate(
        _intent(), replace(_snapshot(), **{timestamp_field: stale})
    )

    assert decision.allowed is False
    assert f"dependency.{name}.stale" in decision.reason_codes


def test_future_dependency_timestamp_is_clock_skew_not_freshness():
    decision = UnifiedOrderGate().evaluate(
        _intent(),
        replace(_snapshot(), cash_at=NOW + timedelta(seconds=30)),
    )

    assert decision.allowed is False
    assert "dependency.cash.clock_skew" in decision.reason_codes


def test_reduce_only_ignores_stale_cash_but_requires_fresh_persistence():
    stale = NOW - timedelta(minutes=10)
    cash_only = UnifiedOrderGate().evaluate(
        _intent(reduce_only=True), replace(_snapshot(), cash_at=stale)
    )
    persistence = UnifiedOrderGate().evaluate(
        _intent(reduce_only=True),
        replace(_snapshot(), persistence_at=stale),
    )

    assert cash_only.allowed is True
    assert persistence.allowed is False
    assert "dependency.persistence.stale" in persistence.reason_codes


def test_intent_cannot_bind_fresh_timestamp_to_a_different_price():
    decision = UnifiedOrderGate().evaluate(
        _intent(), replace(_snapshot(), quote_price=Decimal("110"))
    )

    assert decision.allowed is False
    assert "quote.reference_price_mismatch" in decision.reason_codes


def test_broker_snapshot_builder_never_uses_scalar_or_intent_price_as_quote():
    broker_path = Path(__file__).parents[1] / "broker.py"
    tree = ast.parse(broker_path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_live_order_dependency_snapshot"
    )
    source = ast.get_source_segment(
        broker_path.read_text(encoding="utf-8"), function
    )

    assert "intent.reference_price" not in source
    assert "_last_prices" not in source
    assert "_market_marks" in source
