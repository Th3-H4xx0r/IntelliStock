"""Typed, immutable values crossing the live stock-order boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional


class Health(str, Enum):
    """Tri-state dependency health used to make unknown explicit."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderSource(str, Enum):
    STRATEGY = "strategy"
    MANUAL = "manual"
    RISK_EXIT = "risk_exit"
    RESIDUAL_SLEEVE = "residual_sleeve"


class LifecycleState(str, Enum):
    """Durable states for one immutable client-order identity."""

    INTENT = "intent"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    ACKNOWLEDGED = "acknowledged"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_LIFECYCLE_STATES = frozenset(
    {
        LifecycleState.FILLED,
        LifecycleState.CANCELED,
        LifecycleState.REJECTED,
        LifecycleState.EXPIRED,
    }
)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value, field_name: str, *, positive: bool = False) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return result


def _optional_decimal(value, field_name: str) -> Optional[Decimal]:
    if value is None:
        return None
    return _decimal(value, field_name)


def _normalized_decimal(value: Decimal) -> str:
    # ``format(..., 'f')`` avoids exponent spellings changing the hash.
    rendered = format(value.normalize(), "f")
    return "0" if rendered in ("-0", "") else rendered


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """One immutable request to change live stock exposure."""

    account_id: str
    instance_id: str
    source: OrderSource
    reason: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    reduce_only: bool
    decision_at: datetime
    quote_at: datetime
    risk_snapshot_id: str
    retry_ordinal: int = 0
    order_type: str = "market"
    limit_price: Optional[Decimal] = None
    tif: str = "day"
    extended_hours: bool = False
    reference_price: Optional[Decimal] = None
    idempotency_key: str = field(init=False)

    def __post_init__(self) -> None:
        account_id = str(self.account_id or "").strip()
        instance_id = str(self.instance_id or "").strip()
        reason = str(self.reason or "").strip()
        symbol = str(self.symbol or "").strip().upper()
        risk_snapshot_id = str(self.risk_snapshot_id or "").strip()
        if not account_id:
            raise ValueError("account_id is required")
        if not instance_id:
            raise ValueError("instance_id is required")
        if not reason:
            raise ValueError("reason is required")
        if not symbol:
            raise ValueError("symbol is required")
        if not risk_snapshot_id:
            raise ValueError("risk_snapshot_id is required")

        try:
            source = self.source if isinstance(self.source, OrderSource) else OrderSource(self.source)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported order source: {self.source!r}") from exc
        try:
            side = self.side if isinstance(self.side, OrderSide) else OrderSide(str(self.side).lower())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported order side: {self.side!r}") from exc
        quantity = _decimal(self.quantity, "quantity", positive=True)
        decision_at = _aware_utc(self.decision_at, "decision_at")
        quote_at = _aware_utc(self.quote_at, "quote_at")
        try:
            retry_ordinal = int(self.retry_ordinal)
        except (TypeError, ValueError) as exc:
            raise TypeError("retry_ordinal must be an integer") from exc
        if retry_ordinal < 0 or retry_ordinal != self.retry_ordinal:
            raise ValueError("retry_ordinal must be a non-negative integer")

        order_type = str(self.order_type or "").strip().lower()
        if order_type not in ("market", "limit"):
            raise ValueError("order_type must be market or limit")
        limit_price = _optional_decimal(self.limit_price, "limit_price")
        if order_type == "limit":
            if limit_price is None or limit_price <= 0:
                raise ValueError("limit orders require limit_price > 0")
        else:
            limit_price = None
        tif = str(self.tif or "").strip().lower()
        if tif not in ("day", "gtc", "ioc", "fok"):
            raise ValueError(f"unsupported tif: {tif!r}")
        extended_hours = bool(self.extended_hours)
        if extended_hours and (order_type != "limit" or tif != "day"):
            raise ValueError("extended_hours requires a limit DAY order")
        reference_price = _optional_decimal(self.reference_price, "reference_price")
        if reference_price is not None and reference_price <= 0:
            raise ValueError("reference_price must be > 0")

        for name, value in (
            ("account_id", account_id),
            ("instance_id", instance_id),
            ("source", source),
            ("reason", reason),
            ("symbol", symbol),
            ("side", side),
            ("quantity", quantity),
            ("reduce_only", bool(self.reduce_only)),
            ("decision_at", decision_at),
            ("quote_at", quote_at),
            ("risk_snapshot_id", risk_snapshot_id),
            ("retry_ordinal", retry_ordinal),
            ("order_type", order_type),
            ("limit_price", limit_price),
            ("tif", tif),
            ("extended_hours", extended_hours),
            ("reference_price", reference_price),
        ):
            object.__setattr__(self, name, value)

        canonical = {
            "account_id": account_id,
            "instance_id": instance_id,
            "source": source.value,
            "reason": reason,
            "symbol": symbol,
            "side": side.value,
            "quantity": _normalized_decimal(quantity),
            "reduce_only": bool(self.reduce_only),
            "decision_at": decision_at.isoformat(timespec="microseconds"),
            "quote_at": quote_at.isoformat(timespec="microseconds"),
            "risk_snapshot_id": risk_snapshot_id,
            "retry_ordinal": retry_ordinal,
            "order_type": order_type,
            "limit_price": (
                _normalized_decimal(limit_price) if limit_price is not None else None
            ),
            "tif": tif,
            "extended_hours": extended_hours,
            "reference_price": (
                _normalized_decimal(reference_price)
                if reference_price is not None
                else None
            ),
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        # Preserve the existing clean-room classifier contract: every
        # strategy-owned WAL row begins with this instance's 8-char prefix.
        instance_prefix = re.sub(r"[^A-Za-z0-9]", "", instance_id)[:8] or "x"
        retry_suffix = f"-{retry_ordinal}"
        digest_room = 48 - len(instance_prefix) - 1 - len(retry_suffix)
        key = f"{instance_prefix}-{digest[:digest_room]}{retry_suffix}"
        object.__setattr__(self, "idempotency_key", key)


@dataclass(frozen=True, slots=True)
class DependencySnapshot:
    """One coherent, read-only dependency view evaluated by the gate."""

    account_id: str
    instance_id: str
    observed_at: datetime
    armed: bool
    kill_switch: Health
    quote: Health
    cash: Health
    positions: Health
    calendar: Health
    persistence: Health
    risk_state: Health
    watchdog: Health
    quote_symbol: str
    quote_price: Decimal
    quote_at: datetime
    position_symbol: str
    position_quantity: Decimal
    positions_at: datetime
    available_cash: Decimal
    market_open: bool
    risk_snapshot_id: str
    kill_switch_at: Optional[datetime] = None
    cash_at: Optional[datetime] = None
    calendar_at: Optional[datetime] = None
    persistence_at: Optional[datetime] = None
    risk_state_at: Optional[datetime] = None
    watchdog_at: Optional[datetime] = None
    max_order_notional: Optional[Decimal] = None
    max_position_quantity: Optional[Decimal] = None
    open_order_idempotency_keys: frozenset[str] = field(default_factory=frozenset)
    authorized_sources: frozenset[OrderSource] = field(
        default_factory=lambda: frozenset(OrderSource)
    )
    max_quote_age: timedelta = timedelta(seconds=30)
    max_positions_age: timedelta = timedelta(seconds=60)
    max_cash_age: timedelta = timedelta(seconds=60)
    max_calendar_age: timedelta = timedelta(seconds=60)
    max_control_age: timedelta = timedelta(seconds=60)
    max_clock_skew: timedelta = timedelta(seconds=5)
    max_reference_price_deviation: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        health_fields = (
            "kill_switch",
            "quote",
            "cash",
            "positions",
            "calendar",
            "persistence",
            "risk_state",
            "watchdog",
        )
        for name in health_fields:
            value = getattr(self, name)
            try:
                health = value if isinstance(value, Health) else Health(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a Health value") from exc
            object.__setattr__(self, name, health)

        observed_at = _aware_utc(self.observed_at, "observed_at")
        quote_at = _aware_utc(self.quote_at, "quote_at")
        positions_at = _aware_utc(self.positions_at, "positions_at")
        dependency_times = {}
        for name in (
            "kill_switch_at",
            "cash_at",
            "calendar_at",
            "persistence_at",
            "risk_state_at",
            "watchdog_at",
        ):
            value = getattr(self, name)
            dependency_times[name] = (
                _aware_utc(value, name) if value is not None else None
            )
        quote_price = _decimal(self.quote_price, "quote_price")
        position_quantity = _decimal(self.position_quantity, "position_quantity")
        available_cash = _decimal(self.available_cash, "available_cash")
        max_order_notional = _optional_decimal(
            self.max_order_notional, "max_order_notional"
        )
        max_position_quantity = _optional_decimal(
            self.max_position_quantity, "max_position_quantity"
        )
        max_reference_price_deviation = _decimal(
            self.max_reference_price_deviation,
            "max_reference_price_deviation",
        )
        if position_quantity < 0:
            raise ValueError("position_quantity must be >= 0")
        if available_cash < 0:
            raise ValueError("available_cash must be >= 0")
        if max_order_notional is not None and max_order_notional <= 0:
            raise ValueError("max_order_notional must be > 0")
        if max_position_quantity is not None and max_position_quantity < 0:
            raise ValueError("max_position_quantity must be >= 0")
        if max_reference_price_deviation < 0:
            raise ValueError("max_reference_price_deviation must be >= 0")
        if not isinstance(self.max_quote_age, timedelta) or self.max_quote_age.total_seconds() < 0:
            raise ValueError("max_quote_age must be a non-negative timedelta")
        if not isinstance(self.max_positions_age, timedelta) or self.max_positions_age.total_seconds() < 0:
            raise ValueError("max_positions_age must be a non-negative timedelta")
        for name in (
            "max_cash_age",
            "max_calendar_age",
            "max_control_age",
            "max_clock_skew",
        ):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value.total_seconds() < 0:
                raise ValueError(f"{name} must be a non-negative timedelta")

        try:
            sources = frozenset(
                value if isinstance(value, OrderSource) else OrderSource(value)
                for value in self.authorized_sources
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("authorized_sources contains an unsupported source") from exc

        for name, value in (
            ("account_id", str(self.account_id or "").strip()),
            ("instance_id", str(self.instance_id or "").strip()),
            ("observed_at", observed_at),
            ("armed", bool(self.armed)),
            ("quote_symbol", str(self.quote_symbol or "").strip().upper()),
            ("quote_price", quote_price),
            ("quote_at", quote_at),
            ("position_symbol", str(self.position_symbol or "").strip().upper()),
            ("position_quantity", position_quantity),
            ("positions_at", positions_at),
            *dependency_times.items(),
            ("available_cash", available_cash),
            ("market_open", bool(self.market_open)),
            ("risk_snapshot_id", str(self.risk_snapshot_id or "").strip()),
            ("max_order_notional", max_order_notional),
            ("max_position_quantity", max_position_quantity),
            (
                "max_reference_price_deviation",
                max_reference_price_deviation,
            ),
            (
                "open_order_idempotency_keys",
                frozenset(str(value) for value in self.open_order_idempotency_keys),
            ),
            ("authorized_sources", sources),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Pure gate output. A denied decision never approves transport quantity."""

    allowed: bool
    approved_quantity: Decimal
    reason_codes: tuple[str, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        approved_quantity = _decimal(self.approved_quantity, "approved_quantity")
        if approved_quantity < 0:
            raise ValueError("approved_quantity must be >= 0")
        object.__setattr__(self, "allowed", bool(self.allowed))
        object.__setattr__(self, "approved_quantity", approved_quantity)
        object.__setattr__(
            self, "reason_codes", tuple(str(code) for code in self.reason_codes)
        )
        object.__setattr__(self, "idempotency_key", str(self.idempotency_key))

    @property
    def reasons(self) -> tuple[str, ...]:
        """Compatibility/readability alias for callers displaying denials."""

        return self.reason_codes


@dataclass(frozen=True, slots=True)
class BrokerOrderEvent:
    """Immutable normalized broker event.

    Broker feeds supply cumulative quantities and average prices. The order
    service fills the incremental fields before persistence/accounting.
    """

    event_id: str
    account_id: str
    instance_id: str
    client_order_id: str
    broker_order_id: Optional[str]
    symbol: str
    side: OrderSide
    state: LifecycleState
    cumulative_quantity: Decimal
    cumulative_average_price: Optional[Decimal]
    cumulative_fees: Decimal
    occurred_at: datetime
    sequence: int = -1
    incremental_quantity: Decimal = Decimal("0")
    incremental_price: Optional[Decimal] = None
    incremental_fees: Decimal = Decimal("0")
    reason: str = ""

    def __post_init__(self) -> None:
        strings = {
            "event_id": self.event_id,
            "account_id": self.account_id,
            "instance_id": self.instance_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
        }
        for name, raw in strings.items():
            value = str(raw or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            if name == "symbol":
                value = value.upper()
            object.__setattr__(self, name, value)
        broker_order_id = str(self.broker_order_id or "").strip() or None
        object.__setattr__(self, "broker_order_id", broker_order_id)
        try:
            side = self.side if isinstance(self.side, OrderSide) else OrderSide(
                str(self.side).lower()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported order side: {self.side!r}") from exc
        try:
            state = (
                self.state
                if isinstance(self.state, LifecycleState)
                else LifecycleState(str(self.state).lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported lifecycle state: {self.state!r}") from exc
        cumulative = _decimal(self.cumulative_quantity, "cumulative_quantity")
        incremental = _decimal(
            self.incremental_quantity, "incremental_quantity"
        )
        cumulative_fees = _decimal(self.cumulative_fees, "cumulative_fees")
        incremental_fees = _decimal(self.incremental_fees, "incremental_fees")
        average = _optional_decimal(
            self.cumulative_average_price, "cumulative_average_price"
        )
        incremental_price = _optional_decimal(
            self.incremental_price, "incremental_price"
        )
        if min(cumulative, incremental, cumulative_fees, incremental_fees) < 0:
            raise ValueError("event quantities and fees must be non-negative")
        if average is not None and average <= 0:
            raise ValueError("cumulative_average_price must be > 0")
        if incremental_price is not None and incremental_price <= 0:
            raise ValueError("incremental_price must be > 0")
        if state in (LifecycleState.PARTIAL, LifecycleState.FILLED):
            if cumulative <= 0 or average is None:
                raise ValueError("fill events require cumulative quantity and price")
        try:
            sequence = int(self.sequence)
        except (TypeError, ValueError) as exc:
            raise TypeError("sequence must be an integer") from exc
        if sequence < -1 or sequence != self.sequence:
            raise ValueError("sequence must be -1 or a non-negative integer")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "cumulative_quantity", cumulative)
        object.__setattr__(self, "cumulative_average_price", average)
        object.__setattr__(self, "cumulative_fees", cumulative_fees)
        object.__setattr__(self, "incremental_quantity", incremental)
        object.__setattr__(self, "incremental_price", incremental_price)
        object.__setattr__(self, "incremental_fees", incremental_fees)
        object.__setattr__(self, "occurred_at", _aware_utc(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "reason", str(self.reason or "").strip())

    def with_identity(self, **changes) -> "BrokerOrderEvent":
        """Return an immutable variant; useful to test identity drift."""

        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class ConfirmedFill:
    """Exactly-once incremental accounting instruction."""

    event: BrokerOrderEvent
    incremental_quantity: Decimal
    incremental_price: Decimal
    incremental_fees: Decimal
    position_delta: Decimal
    cash_delta: Decimal

    def __post_init__(self) -> None:
        quantity = _decimal(
            self.incremental_quantity, "incremental_quantity", positive=True
        )
        price = _decimal(self.incremental_price, "incremental_price", positive=True)
        fees = _decimal(self.incremental_fees, "incremental_fees")
        position_delta = _decimal(self.position_delta, "position_delta")
        cash_delta = _decimal(self.cash_delta, "cash_delta")
        if fees < 0:
            raise ValueError("incremental_fees must be non-negative")
        object.__setattr__(self, "incremental_quantity", quantity)
        object.__setattr__(self, "incremental_price", price)
        object.__setattr__(self, "incremental_fees", fees)
        object.__setattr__(self, "position_delta", position_delta)
        object.__setattr__(self, "cash_delta", cash_delta)
