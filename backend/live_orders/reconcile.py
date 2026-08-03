"""Broker-first startup and continuous Alpaca reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Callable, Mapping, Optional

from .store import OrderLifecycleStore
from .types import (
    BrokerOrderEvent,
    LifecycleState,
    OrderSide,
    TERMINAL_LIFECYCLE_STATES,
)


def _decimal(value, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise TypeError(f"{name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class BrokerPositionSnapshot:
    symbol: str
    quantity: Decimal
    market_value: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        symbol = str(self.symbol or "").strip().upper()
        if not symbol:
            raise ValueError("position symbol is required")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity"))
        object.__setattr__(
            self, "market_value", _decimal(self.market_value, "market_value")
        )


@dataclass(frozen=True, slots=True)
class BrokerOrderSnapshot:
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    requested_quantity: Decimal
    status: str
    cumulative_quantity: Decimal
    cumulative_average_price: Optional[Decimal]
    cumulative_fees: Decimal
    updated_at: datetime

    def __post_init__(self) -> None:
        broker_id = str(self.broker_order_id or "").strip()
        symbol = str(self.symbol or "").strip().upper()
        status = str(self.status or "").strip().lower()
        if not broker_id or not symbol or not status:
            raise ValueError("broker order id, symbol, and status are required")
        try:
            side = self.side if isinstance(self.side, OrderSide) else OrderSide(
                str(self.side).lower()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("broker order side is invalid") from exc
        requested = _decimal(self.requested_quantity, "requested_quantity")
        cumulative = _decimal(self.cumulative_quantity, "cumulative_quantity")
        fees = _decimal(self.cumulative_fees, "cumulative_fees")
        average = (
            _decimal(self.cumulative_average_price, "cumulative_average_price")
            if self.cumulative_average_price is not None
            else None
        )
        if requested < 0 or cumulative < 0 or fees < 0:
            raise ValueError("broker order quantities and fees must be non-negative")
        if average is not None and average <= 0:
            raise ValueError("cumulative average price must be positive")
        object.__setattr__(
            self, "client_order_id", str(self.client_order_id or "").strip()
        )
        object.__setattr__(self, "broker_order_id", broker_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "requested_quantity", requested)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "cumulative_quantity", cumulative)
        object.__setattr__(self, "cumulative_average_price", average)
        object.__setattr__(self, "cumulative_fees", fees)
        object.__setattr__(self, "updated_at", _aware(self.updated_at, "updated_at"))


@dataclass(frozen=True, slots=True)
class AuthoritativeBrokerSnapshot:
    account_id: str
    instance_id: str
    observed_at: datetime
    positions: tuple[BrokerPositionSnapshot, ...]
    orders: tuple[BrokerOrderSnapshot, ...]
    broker_available: bool
    positions_stable: bool
    orders_complete: bool

    def __post_init__(self) -> None:
        account_id = str(self.account_id or "").strip()
        instance_id = str(self.instance_id or "").strip()
        if not account_id or not instance_id:
            raise ValueError("snapshot account_id and instance_id are required")
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "instance_id", instance_id)
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at"))
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "broker_available", bool(self.broker_available))
        object.__setattr__(self, "positions_stable", bool(self.positions_stable))
        object.__setattr__(self, "orders_complete", bool(self.orders_complete))


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    events: tuple[BrokerOrderEvent, ...]
    owned: Mapping[str, Decimal]
    external: Mapping[str, Decimal]
    unresolved: Mapping[str, Decimal]
    healthy: bool
    evidence_hash: str
    issues: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("owned", "external", "unresolved"):
            normalized = {
                str(symbol).upper(): _decimal(quantity, f"{name}.{symbol}")
                for symbol, quantity in dict(getattr(self, name)).items()
                if _decimal(quantity, f"{name}.{symbol}") != 0
            }
            object.__setattr__(self, name, MappingProxyType(normalized))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "issues", tuple(str(issue) for issue in self.issues))
        object.__setattr__(self, "healthy", bool(self.healthy))
        object.__setattr__(self, "evidence_hash", str(self.evidence_hash))


_STATUS_STATES = {
    "new": LifecycleState.ACKNOWLEDGED,
    "accepted": LifecycleState.ACKNOWLEDGED,
    "pending_new": LifecycleState.ACKNOWLEDGED,
    "partially_filled": LifecycleState.PARTIAL,
    "partial_fill": LifecycleState.PARTIAL,
    "filled": LifecycleState.FILLED,
    "canceled": LifecycleState.CANCELED,
    "cancelled": LifecycleState.CANCELED,
    "rejected": LifecycleState.REJECTED,
    "expired": LifecycleState.EXPIRED,
    "done_for_day": LifecycleState.EXPIRED,
}


def _event_from_order(
    snapshot: AuthoritativeBrokerSnapshot, order: BrokerOrderSnapshot
) -> Optional[BrokerOrderEvent]:
    state = _STATUS_STATES.get(order.status)
    if state is None:
        return None
    identity = "|".join(
        (
            order.client_order_id,
            order.broker_order_id,
            state.value,
            str(order.cumulative_quantity.normalize()),
            str(order.cumulative_fees.normalize()),
        )
    )
    return BrokerOrderEvent(
        event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        account_id=snapshot.account_id,
        instance_id=snapshot.instance_id,
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        symbol=order.symbol,
        side=order.side,
        state=state,
        cumulative_quantity=order.cumulative_quantity,
        cumulative_average_price=order.cumulative_average_price,
        cumulative_fees=order.cumulative_fees,
        occurred_at=order.updated_at,
        reason=f"broker reconciliation: {order.status}",
    )


def _result_hash(
    snapshot: AuthoritativeBrokerSnapshot,
    *,
    events,
    owned,
    external,
    unresolved,
    issues,
) -> str:
    payload = {
        "account_id": snapshot.account_id,
        "instance_id": snapshot.instance_id,
        "observed_at": snapshot.observed_at.isoformat(),
        "flags": [
            snapshot.broker_available,
            snapshot.positions_stable,
            snapshot.orders_complete,
        ],
        "positions": [
            [position.symbol, str(position.quantity), str(position.market_value)]
            for position in sorted(snapshot.positions, key=lambda item: item.symbol)
        ],
        "orders": [
            [
                order.client_order_id,
                order.broker_order_id,
                order.symbol,
                order.side.value,
                order.status,
                str(order.cumulative_quantity),
            ]
            for order in sorted(
                snapshot.orders,
                key=lambda item: (item.client_order_id, item.broker_order_id),
            )
        ],
        "events": [event.event_id for event in events],
        "owned": sorted((key, str(value)) for key, value in owned.items()),
        "external": sorted((key, str(value)) for key, value in external.items()),
        "unresolved": sorted(
            (key, str(value)) for key, value in unresolved.items()
        ),
        "issues": list(issues),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


DEFAULT_ABANDONED_ORDER_GRACE = timedelta(minutes=2)


def _abandoned_stub_event(
    snapshot: AuthoritativeBrokerSnapshot, record
) -> BrokerOrderEvent:
    return BrokerOrderEvent(
        event_id=hashlib.sha256(
            "|".join(
                (
                    "abandoned",
                    record.client_order_id,
                    record.state.value,
                    snapshot.observed_at.isoformat(),
                )
            ).encode("utf-8")
        ).hexdigest(),
        account_id=record.intent.account_id,
        instance_id=record.intent.instance_id,
        client_order_id=record.client_order_id,
        broker_order_id=None,
        symbol=record.intent.symbol,
        side=record.intent.side,
        state=LifecycleState.EXPIRED,
        cumulative_quantity=Decimal("0"),
        cumulative_average_price=None,
        cumulative_fees=Decimal("0"),
        occurred_at=snapshot.observed_at,
        reason=(
            "broker reconciliation: never acknowledged and absent from a "
            "complete broker snapshot"
        ),
    )


class StartupReconciler:
    """Reconcile broker truth into the lifecycle before classifying ownership."""

    def __init__(
        self,
        *,
        lifecycle_store: OrderLifecycleStore,
        event_applier: Callable[[BrokerOrderEvent], object],
        abandoned_grace: timedelta = DEFAULT_ABANDONED_ORDER_GRACE,
        cid_prefix: str = "",
    ) -> None:
        self.lifecycle_store = lifecycle_store
        self.event_applier = event_applier
        self.abandoned_grace = abandoned_grace
        # Client-order-id prefix this instance mints (make_client_order_id emits
        # "{instance[:8]}-{digest}-{retry}"). Used to tell OUR orders from
        # everyone else's. Empty means "cannot tell", and we fall back to
        # treating unknown orders as issues, i.e. today's behaviour.
        self.cid_prefix = str(cid_prefix or "").strip()

    def _is_external_order(self, order) -> bool:
        """True when a broker order was placed by something other than this
        instance's LiveOrderService.

        2026-08-03 adversarial sweep, HIGH: an order with no local lifecycle
        record raised `unknown_broker_order`, which set healthy=False, which
        made the gate report positions "unhealthy" -- and `positions` is
        REQUIRED even for reduce_only, so every risk exit was denied on a
        60-second loop for as long as that order sat in the history window.

        A manual order placed in the Alpaca UI, or one from the legacy
        adapter.buy()/WAL path, is not evidence that OUR lifecycle is corrupt.
        alpaca-main traded for months through the legacy path, so this was
        likely to fire on the first tick after deploy and block exits on a live
        account. External orders are recorded as informational, never as an
        issue that gates protective sells.
        """
        if not self.cid_prefix:
            return False
        cid = str(getattr(order, "client_order_id", "") or "")
        return not cid.startswith(self.cid_prefix)

    def _resolve_abandoned(
        self, snapshot: AuthoritativeBrokerSnapshot, record
    ) -> bool:
        """Retire a local order the broker has never heard of. Returns True
        when the record was closed out and must not raise an issue.

        A row that reached UNKNOWN (ambiguous POST) or never left
        INTENT/SUBMITTING and is absent from an authoritative, complete broker
        snapshot describes an order that does not exist. Left alone it is
        non-terminal forever, so every reconcile raised
        ``local_order_missing_broker``, ``healthy`` stayed False, and the
        trading cycle — including risk exits — was skipped. One dropped POST
        was a permanent inability to sell.

        Only rows with NO broker order id and NO filled quantity qualify.
        Those contribute nothing to lineage, so retiring them cannot lose a
        position; anything the broker did acknowledge stays an open issue,
        because that really is a discrepancy a human must see.
        """

        if record.broker_order_id or record.cumulative_quantity > 0:
            return False
        if record.state not in (
            LifecycleState.INTENT,
            LifecycleState.SUBMITTING,
            LifecycleState.UNKNOWN,
        ):
            return False
        last_seen = max(event.occurred_at for event in record.events)
        if snapshot.observed_at - last_seen < self.abandoned_grace:
            # Still inside the window where a genuinely-accepted order may not
            # have surfaced in the orders endpoint yet. Fail closed for now.
            return False
        try:
            self.event_applier(_abandoned_stub_event(snapshot, record))
        except Exception:
            return False
        return True

    def reconcile(
        self, snapshot: AuthoritativeBrokerSnapshot
    ) -> ReconciliationResult:
        positions = {
            position.symbol: position.quantity
            for position in snapshot.positions
            if position.quantity != 0
        }
        if (
            not snapshot.broker_available
            or not snapshot.positions_stable
            or not snapshot.orders_complete
        ):
            issues = []
            if not snapshot.broker_available:
                issues.append("broker_unavailable")
            if not snapshot.positions_stable:
                issues.append("positions_changed_during_snapshot")
            if not snapshot.orders_complete:
                issues.append("orders_truncated")
            unresolved = dict(positions)
            events: tuple[BrokerOrderEvent, ...] = ()
            digest = _result_hash(
                snapshot,
                events=events,
                owned={},
                external={},
                unresolved=unresolved,
                issues=issues,
            )
            return ReconciliationResult(
                events=events,
                owned={},
                external={},
                unresolved=unresolved,
                healthy=False,
                evidence_hash=digest,
                issues=tuple(issues),
            )

        records = {
            record.client_order_id: record
            for record in self.lifecycle_store.list_for_instance(
                snapshot.instance_id
            )
            if record.intent.account_id == snapshot.account_id
        }
        issues: list[str] = []
        unresolved: dict[str, Decimal] = {}
        seen_known: set[str] = set()
        external: list[str] = []
        for order in snapshot.orders:
            record = records.get(order.client_order_id)
            if record is None:
                quantity = order.cumulative_quantity or order.requested_quantity
                unresolved[order.symbol] = max(
                    unresolved.get(order.symbol, Decimal("0")), quantity
                )
                if self._is_external_order(order):
                    # Informational only -- an order we did not place cannot
                    # prove our lifecycle is wrong, and treating it as an issue
                    # blocks risk exits (see _is_external_order).
                    external.append(str(order.broker_order_id))
                else:
                    issues.append(
                        f"unknown_broker_order:{order.broker_order_id}"
                    )
                continue
            seen_known.add(order.client_order_id)
            if (
                record.intent.symbol != order.symbol
                or record.intent.side != order.side
                or (
                    record.broker_order_id
                    and record.broker_order_id != order.broker_order_id
                )
            ):
                unresolved[order.symbol] = max(
                    unresolved.get(order.symbol, Decimal("0")),
                    order.cumulative_quantity or order.requested_quantity,
                )
                issues.append(f"order_identity_mismatch:{order.client_order_id}")
                continue
            normalized = _event_from_order(snapshot, order)
            if normalized is None:
                issues.append(
                    f"unsupported_broker_status:{order.client_order_id}:{order.status}"
                )
                continue
            self.event_applier(normalized)

        records = {
            record.client_order_id: record
            for record in self.lifecycle_store.list_for_instance(
                snapshot.instance_id
            )
            if record.intent.account_id == snapshot.account_id
        }
        retired = False
        for cid, record in records.items():
            if record.terminal or cid in seen_known:
                continue
            if self._resolve_abandoned(snapshot, record):
                retired = True
                continue
            unresolved[record.intent.symbol] = max(
                unresolved.get(record.intent.symbol, Decimal("0")),
                record.intent.quantity - record.cumulative_quantity,
            )
            issues.append(f"local_order_missing_broker:{cid}")

        if retired:
            records = {
                record.client_order_id: record
                for record in self.lifecycle_store.list_for_instance(
                    snapshot.instance_id
                )
                if record.intent.account_id == snapshot.account_id
            }

        lineage: dict[str, Decimal] = {}
        all_events: list[BrokerOrderEvent] = []
        for record in records.values():
            all_events.extend(record.events)
            signed = (
                record.cumulative_quantity
                if record.intent.side is OrderSide.BUY
                else -record.cumulative_quantity
            )
            lineage[record.intent.symbol] = (
                lineage.get(record.intent.symbol, Decimal("0")) + signed
            )

        owned: dict[str, Decimal] = {}
        external: dict[str, Decimal] = {}
        for symbol, broker_quantity in positions.items():
            if broker_quantity < 0:
                external[symbol] = broker_quantity
                continue
            proven = max(Decimal("0"), lineage.get(symbol, Decimal("0")))
            strategy_quantity = min(broker_quantity, proven)
            if strategy_quantity > 0:
                owned[symbol] = strategy_quantity
            excess = broker_quantity - strategy_quantity
            if excess > 0:
                external[symbol] = excess
            if proven > broker_quantity:
                unresolved[symbol] = max(
                    unresolved.get(symbol, Decimal("0")),
                    proven - broker_quantity,
                )
                issues.append(f"broker_quantity_below_lineage:{symbol}")
        for symbol, proven in lineage.items():
            if proven > 0 and symbol not in positions:
                unresolved[symbol] = max(
                    unresolved.get(symbol, Decimal("0")), proven
                )
                issues.append(f"lineage_position_missing_broker:{symbol}")

        all_events.sort(
            key=lambda event: (
                event.occurred_at,
                event.client_order_id,
                event.sequence,
            )
        )
        healthy = not issues
        digest = _result_hash(
            snapshot,
            events=all_events,
            owned=owned,
            external=external,
            unresolved=unresolved,
            issues=issues,
        )
        return ReconciliationResult(
            events=tuple(all_events),
            owned=owned,
            external=external,
            unresolved=unresolved,
            healthy=healthy,
            evidence_hash=digest,
            issues=tuple(issues),
        )
