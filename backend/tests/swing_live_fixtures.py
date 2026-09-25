"""Intent and snapshot builders for the swing-port option and bracket tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from live_orders import (
    DependencySnapshot,
    Health,
    OrderIntent,
    OrderSide,
    OrderSource,
)

#: Monday 2026-10-05 11:00 ET, a regular session.
RTH = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
OCC = "APH261009P00130000"

_HEALTH = ("kill_switch", "quote", "cash", "positions", "calendar",
           "persistence", "risk_state", "watchdog")
_STAMPS = ("kill_switch_at", "cash_at", "calendar_at", "persistence_at",
           "risk_state_at", "watchdog_at")


def option_intent(**changes) -> OrderIntent:
    values = dict(
        account_id="acct-1", instance_id="instance-1",
        source=OrderSource.STRATEGY, reason="wheel_sto_put", symbol=OCC,
        side=OrderSide.SELL, quantity=Decimal("1"), reduce_only=False,
        decision_at=RTH, quote_at=RTH, risk_snapshot_id="risk-1",
        order_type="limit", limit_price=Decimal("1.20"), tif="day",
        asset_class="us_option", position_intent="sell_to_open",
        contract_multiplier=100, underlying="APH", option_type="put",
        strike=Decimal("130"), expiry="2026-10-09",
    )
    values.update(changes)
    return OrderIntent(**values)


def buy_to_close(**changes) -> OrderIntent:
    values = dict(side=OrderSide.BUY, reduce_only=True,
                  position_intent="buy_to_close", reason="wheel_btc_itm",
                  source=OrderSource.RISK_EXIT, order_type="market",
                  limit_price=None)
    values.update(changes)
    return option_intent(**values)


def option_snapshot(order, **changes) -> DependencySnapshot:
    values = dict(
        account_id=order.account_id, instance_id=order.instance_id,
        observed_at=RTH, armed=True,
        quote_symbol=order.symbol, quote_price=Decimal("1.20"),
        quote_at=order.quote_at, position_symbol=order.symbol,
        position_quantity=Decimal("0"), positions_at=RTH,
        available_cash=Decimal("20000"), market_open=True,
        risk_snapshot_id=order.risk_snapshot_id,
        max_order_notional=Decimal("10000"), max_position_quantity=None,
        max_quote_age=timedelta(seconds=60),
        asset_class="us_option", regular_session_open=True,
        account_equity=Decimal("60000"),
        open_short_put_collateral=Decimal("0"),
        pending_sell_to_open_collateral=Decimal("0"),
        underlying_put_collateral=Decimal("0"),
    )
    values.update({name: Health.HEALTHY for name in _HEALTH})
    values.update({name: RTH for name in _STAMPS})
    values.update(changes)
    return DependencySnapshot(**values)


def bracket_intent(**changes) -> OrderIntent:
    values = dict(
        account_id="acct-1", instance_id="instance-1",
        source=OrderSource.STRATEGY, reason="swing_entry", symbol="AAPL",
        side=OrderSide.BUY, quantity=Decimal("5"), reduce_only=False,
        decision_at=RTH, quote_at=RTH, risk_snapshot_id="risk-1",
        order_type="market", tif="gtc", reference_price=Decimal("100"),
        order_class="bracket", take_profit_price=Decimal("109"),
        stop_loss_price=Decimal("94"),
    )
    values.update(changes)
    return OrderIntent(**values)


def equity_snapshot(order, **changes) -> DependencySnapshot:
    values = dict(
        account_id=order.account_id, instance_id=order.instance_id,
        observed_at=RTH, armed=True, quote_symbol=order.symbol,
        quote_price=Decimal("100"), quote_at=order.quote_at,
        position_symbol=order.symbol, position_quantity=Decimal("0"),
        positions_at=RTH, available_cash=Decimal("10000"), market_open=True,
        risk_snapshot_id=order.risk_snapshot_id,
        max_order_notional=Decimal("10000"),
        max_position_quantity=Decimal("1000"),
    )
    values.update({name: Health.HEALTHY for name in _HEALTH})
    values.update({name: RTH for name in _STAMPS})
    values.update(changes)
    return DependencySnapshot(**values)


def leg_ref(cid, kind, *, status="held", filled_qty="0",
            filled_avg_price=None, qty="5", symbol="AAPL"):
    """One bracket child leg as the adapter's OrderRef shape."""
    return SimpleNamespace(
        broker_order_id=f"broker-{cid}", id=f"broker-{cid}",
        client_order_id=cid, symbol=symbol, side="sell", qty=qty,
        status=status, filled_qty=filled_qty,
        filled_avg_price=filled_avg_price, order_type=kind,
        order_class="bracket",
        limit_price=109.0 if kind == "limit" else None,
        stop_price=94.0 if kind == "stop" else None)


def parent_ref(order, *, status="accepted", filled_qty="0",
               filled_avg_price=None):
    return SimpleNamespace(
        client_order_id=order.idempotency_key,
        broker_order_id="broker-parent", id="broker-parent",
        symbol=order.symbol, side=order.side.value,
        qty=float(order.quantity), status=status, filled_qty=filled_qty,
        filled_avg_price=filled_avg_price, order_class="bracket")
