from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from live_orders import (
    BrokerOrderEvent,
    DependencySnapshot,
    Health,
    LifecycleState,
    OrderIntent,
    OrderSide,
    OrderSource,
)


NOW = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)


def intent(**changes) -> OrderIntent:
    values = {
        "account_id": "acct-1",
        "instance_id": "instance-1",
        "source": OrderSource.STRATEGY,
        "reason": "allocation",
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "quantity": Decimal("5"),
        "reduce_only": False,
        "decision_at": NOW,
        "quote_at": NOW,
        "risk_snapshot_id": "risk-1",
        "reference_price": Decimal("100"),
    }
    values.update(changes)
    return OrderIntent(**values)


def snapshot(order: OrderIntent | None = None, **changes) -> DependencySnapshot:
    order = order or intent()
    values = {
        "account_id": order.account_id,
        "instance_id": order.instance_id,
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
        "quote_symbol": order.symbol,
        "quote_price": Decimal("100"),
        "quote_at": order.quote_at,
        "position_symbol": order.symbol,
        "position_quantity": Decimal("10"),
        "positions_at": NOW,
        "available_cash": Decimal("1000"),
        "market_open": True,
        "risk_snapshot_id": order.risk_snapshot_id,
        "kill_switch_at": NOW,
        "cash_at": NOW,
        "calendar_at": NOW,
        "persistence_at": NOW,
        "risk_state_at": NOW,
        "watchdog_at": NOW,
        "max_order_notional": Decimal("1000"),
        "max_position_quantity": Decimal("100"),
    }
    values.update(changes)
    return DependencySnapshot(**values)


def broker_ref(
    order: OrderIntent,
    *,
    status: str = "accepted",
    broker_order_id: str = "broker-1",
    filled_qty: Decimal = Decimal("0"),
    filled_avg_price: Decimal | None = None,
):
    return SimpleNamespace(
        client_order_id=order.idempotency_key,
        broker_order_id=broker_order_id,
        id=broker_order_id,
        symbol=order.symbol,
        side=order.side.value,
        qty=float(order.quantity),
        status=status,
        filled_qty=float(filled_qty),
        filled_avg_price=(
            float(filled_avg_price) if filled_avg_price is not None else None
        ),
    )


def event(
    order: OrderIntent,
    *,
    state: LifecycleState,
    cumulative: Decimal = Decimal("0"),
    average: Decimal | None = None,
    fees: Decimal = Decimal("0"),
    event_id: str | None = None,
    broker_order_id: str = "broker-1",
    reason: str = "",
) -> BrokerOrderEvent:
    return BrokerOrderEvent(
        event_id=event_id or f"{state.value}:{cumulative}:{fees}",
        account_id=order.account_id,
        instance_id=order.instance_id,
        client_order_id=order.idempotency_key,
        broker_order_id=broker_order_id,
        symbol=order.symbol,
        side=order.side,
        state=state,
        cumulative_quantity=cumulative,
        cumulative_average_price=average,
        cumulative_fees=fees,
        occurred_at=NOW,
        reason=reason,
    )


def retry(order: OrderIntent, ordinal: int) -> OrderIntent:
    return replace(order, retry_ordinal=ordinal)
