"""Durable live-order orchestration and confirmed-fill accounting."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from .gate import UnifiedOrderGate
from .store import (
    InMemoryLifecycleBackend,
    LifecycleConflict,
    LifecycleRecord,
    OrderLifecycleStore,
)
from .types import (
    BrokerOrderEvent,
    ConfirmedFill,
    DependencySnapshot,
    GateDecision,
    LifecycleState,
    OrderIntent,
    OrderSide,
    TERMINAL_LIFECYCLE_STATES,
)


class TerminalRetryExhausted(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Reservation:
    client_order_id: str
    side: OrderSide
    remaining_quantity: Decimal
    remaining_notional: Decimal


@dataclass(frozen=True, slots=True)
class OrderSubmission:
    decision: GateDecision
    reference: Any = None
    uncertain: bool = False

    @property
    def accepted(self) -> bool:
        return bool(self.decision.allowed and self.reference is not None)


@dataclass(frozen=True, slots=True)
class EventApplication:
    applied: bool
    record: LifecycleRecord
    fill: Optional[ConfirmedFill] = None
    reason: str = ""


def _denial(intent: OrderIntent, reason: str) -> OrderSubmission:
    return OrderSubmission(
        decision=GateDecision(
            allowed=False,
            approved_quantity=Decimal("0"),
            reason_codes=(reason,),
            idempotency_key=intent.idempotency_key,
        )
    )


def _event_id(
    client_order_id: str,
    broker_order_id: Optional[str],
    state: LifecycleState,
    cumulative: Decimal,
    fees: Decimal,
) -> str:
    payload = "|".join(
        (
            client_order_id,
            str(broker_order_id or ""),
            state.value,
            str(cumulative.normalize()),
            str(fees.normalize()),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def definite_broker_rejection(exc: BaseException) -> bool:
    """True when the broker definitively refused to accept the order.

    2026-08-02: a PDT or wash-sale rejection arrives here as a transport
    exception, indistinguishable from a lost acknowledgement, so the service
    recorded ``UNKNOWN`` — a non-terminal state the broker will never resolve
    because the order does not exist. Every later reconcile then reported
    ``local_order_missing_broker``, the cycle was declared unhealthy, and the
    bot could no longer place RISK EXITS. One routine rejection was a
    permanent inability to sell.

    Duck-typed on purpose: live_orders must not import broker adapters, so an
    adapter opts in by setting ``broker_definitive_rejection = True`` on the
    typed errors it raises only when nothing was submitted.
    """

    return getattr(exc, "broker_definitive_rejection", False) is True


def new_retry_intent(
    terminal_intent: OrderIntent, *, reason: str, maximum: int
) -> OrderIntent:
    maximum = int(maximum)
    if terminal_intent.retry_ordinal >= maximum:
        raise TerminalRetryExhausted(
            f"terminal retry maximum {maximum} exhausted"
        )
    return replace(
        terminal_intent,
        reason=str(reason or "").strip() or terminal_intent.reason,
        retry_ordinal=terminal_intent.retry_ordinal + 1,
    )


class LiveOrderService:
    """Single durable authority for every live Alpaca stock order."""

    def __init__(
        self,
        *,
        account_id: str,
        instance_id: str,
        snapshot_provider: Callable[[OrderIntent], DependencySnapshot],
        transport: Callable[..., Any],
        gate: Optional[UnifiedOrderGate] = None,
        lifecycle_store: Optional[OrderLifecycleStore] = None,
        lookup_by_client_id: Optional[Callable[[str], Any]] = None,
        confirmed_fill_handler: Optional[Callable[[ConfirmedFill], None]] = None,
        event_handler: Optional[
            Callable[[BrokerOrderEvent, Optional[ConfirmedFill]], None]
        ] = None,
        max_terminal_retries: int = 2,
    ) -> None:
        self.account_id = str(account_id)
        self.instance_id = str(instance_id)
        self.risk_snapshot_id = ""
        self._snapshot_provider = snapshot_provider
        self._transport = transport
        self._gate = gate or UnifiedOrderGate()
        self.lifecycle_store = lifecycle_store or OrderLifecycleStore(
            InMemoryLifecycleBackend()
        )
        self._lookup_by_client_id = lookup_by_client_id
        self._confirmed_fill_handler = confirmed_fill_handler
        self._event_handler = event_handler
        self.max_terminal_retries = int(max_terminal_retries)
        self._reservations: dict[str, Reservation] = {}
        self.capacity_breaches: set[str] = set()
        self._restore_reservations()

    def _restore_reservations(self) -> None:
        for record in self.lifecycle_store.list_for_instance(self.instance_id):
            if record.terminal:
                continue
            intent = record.intent
            remaining = max(
                Decimal("0"), intent.quantity - record.cumulative_quantity
            )
            reference = intent.reference_price or Decimal("0")
            consumed = Decimal("0")
            if (
                record.cumulative_average_price is not None
                and record.cumulative_quantity > 0
            ):
                consumed = (
                    record.cumulative_average_price * record.cumulative_quantity
                    + record.cumulative_fees
                )
            self._reservations[intent.idempotency_key] = Reservation(
                client_order_id=intent.idempotency_key,
                side=intent.side,
                remaining_quantity=remaining,
                remaining_notional=max(
                    Decimal("0"), intent.quantity * reference - consumed
                ),
            )

    def reservation_for(self, client_order_id: str) -> Optional[Reservation]:
        return self._reservations.get(str(client_order_id))

    def _append_internal(
        self,
        record: LifecycleRecord,
        state: LifecycleState,
        *,
        reason: str = "",
        broker_order_id: Optional[str] = None,
    ) -> LifecycleRecord:
        event = BrokerOrderEvent(
            event_id=f"{state.value}:{record.client_order_id}:{record.version + 1}",
            account_id=record.intent.account_id,
            instance_id=record.intent.instance_id,
            client_order_id=record.client_order_id,
            broker_order_id=broker_order_id or record.broker_order_id,
            symbol=record.intent.symbol,
            side=record.intent.side,
            state=state,
            cumulative_quantity=record.cumulative_quantity,
            cumulative_average_price=record.cumulative_average_price,
            cumulative_fees=record.cumulative_fees,
            occurred_at=datetime.now(timezone.utc),
            reason=reason,
        )
        return self.lifecycle_store.append(
            event, expected_version=record.version
        ).record

    def _event_from_reference(
        self, intent: OrderIntent, reference: Any
    ) -> BrokerOrderEvent:
        status = str(getattr(reference, "status", "accepted") or "accepted").lower()
        state = {
            "filled": LifecycleState.FILLED,
            "partially_filled": LifecycleState.PARTIAL,
            "partial_fill": LifecycleState.PARTIAL,
            "canceled": LifecycleState.CANCELED,
            "cancelled": LifecycleState.CANCELED,
            "rejected": LifecycleState.REJECTED,
            "expired": LifecycleState.EXPIRED,
            "done_for_day": LifecycleState.EXPIRED,
        }.get(status, LifecycleState.ACKNOWLEDGED)
        broker_order_id = str(
            getattr(reference, "broker_order_id", None)
            or getattr(reference, "id", None)
            or intent.idempotency_key
        )
        cumulative = Decimal(str(getattr(reference, "filled_qty", 0) or 0))
        raw_average = getattr(reference, "filled_avg_price", None)
        average = Decimal(str(raw_average)) if raw_average not in (None, 0, "0") else None
        occurred = getattr(reference, "submitted_at_utc", None)
        if not isinstance(occurred, datetime):
            occurred = datetime.now(timezone.utc)
        event_id = _event_id(
            intent.idempotency_key,
            broker_order_id,
            state,
            cumulative,
            Decimal("0"),
        )
        return BrokerOrderEvent(
            event_id=event_id,
            account_id=intent.account_id,
            instance_id=intent.instance_id,
            client_order_id=intent.idempotency_key,
            broker_order_id=broker_order_id,
            symbol=intent.symbol,
            side=intent.side,
            state=state,
            cumulative_quantity=cumulative,
            cumulative_average_price=average,
            cumulative_fees=Decimal("0"),
            occurred_at=occurred,
        )

    def _reconcile_existing(
        self, intent: OrderIntent, record: LifecycleRecord
    ) -> OrderSubmission:
        if record.terminal:
            return _denial(intent, "idempotency.terminal_requires_retry")
        if self._lookup_by_client_id is None:
            return _denial(intent, "idempotency.open_order_exists")
        try:
            reference = self._lookup_by_client_id(intent.idempotency_key)
        except Exception:
            return OrderSubmission(
                decision=GateDecision(
                    allowed=True,
                    approved_quantity=intent.quantity,
                    reason_codes=("broker.reconciliation.unavailable",),
                    idempotency_key=intent.idempotency_key,
                ),
                uncertain=True,
            )
        if reference is None:
            return OrderSubmission(
                decision=GateDecision(
                    allowed=True,
                    approved_quantity=intent.quantity,
                    reason_codes=("broker.order.outcome_unknown",),
                    idempotency_key=intent.idempotency_key,
                ),
                uncertain=True,
            )
        self.apply_broker_event(self._event_from_reference(intent, reference))
        return OrderSubmission(
            decision=GateDecision(
                allowed=True,
                approved_quantity=intent.quantity,
                reason_codes=(),
                idempotency_key=intent.idempotency_key,
            ),
            reference=reference,
        )

    def _live_identity(
        self, intent: OrderIntent
    ) -> tuple[OrderIntent, Optional[LifecycleRecord], str]:
        """Walk to the identity this decision may actually use.

        A terminal record used to end the story: the decision was denied
        ``idempotency.terminal_requires_retry`` forever, and because
        ``new_retry_intent`` had no caller anywhere in the tree there was no
        way out. A rejected exit therefore stayed rejected for as long as the
        decision kept being re-emitted, which is precisely when we most need
        it to go through.

        A terminal record that moved NO quantity proves only that this
        identity is spent, never that the exposure changed, so escalate the
        retry ordinal and try again under a fresh client order id — bounded by
        ``max_terminal_retries`` so a permanently-rejecting order (PDT, wash
        sale) stops after a handful of attempts instead of hammering the
        broker. A record with fills is different: the exposure exists, and
        re-submitting would double it.
        """

        candidate = intent
        while True:
            existing = self.lifecycle_store.get(candidate.idempotency_key)
            if existing is None:
                return candidate, None, ""
            if not existing.intent.same_identity(candidate):
                return candidate, existing, "idempotency.identity_drift"
            if not existing.terminal:
                return candidate, existing, ""
            if (
                existing.state is LifecycleState.FILLED
                or existing.cumulative_quantity > 0
            ):
                return candidate, existing, "idempotency.terminal_requires_retry"
            try:
                candidate = new_retry_intent(
                    candidate,
                    reason=candidate.reason,
                    maximum=self.max_terminal_retries,
                )
            except TerminalRetryExhausted:
                return candidate, existing, "idempotency.terminal_retry_exhausted"

    def submit(self, intent: OrderIntent) -> OrderSubmission:
        if not isinstance(intent, OrderIntent):
            raise TypeError("LiveOrderService.submit requires an OrderIntent")
        intent, existing, denial = self._live_identity(intent)
        if denial:
            return _denial(intent, denial)
        if existing is not None:
            return self._reconcile_existing(intent, existing)
        try:
            snapshot = self._snapshot_provider(intent)
        except Exception:
            return _denial(intent, "dependency.snapshot.unavailable")
        if not isinstance(snapshot, DependencySnapshot):
            return _denial(intent, "dependency.snapshot.invalid")
        decision = self._gate.evaluate(intent, snapshot)
        if not decision.allowed:
            return OrderSubmission(decision=decision)
        try:
            record = self.lifecycle_store.create_intent(intent)
        except Exception:
            return _denial(intent, "persistence.intent.failed")
        self._reservations[intent.idempotency_key] = Reservation(
            client_order_id=intent.idempotency_key,
            side=intent.side,
            remaining_quantity=decision.approved_quantity,
            remaining_notional=decision.approved_quantity * snapshot.quote_price,
        )
        try:
            record = self._append_internal(record, LifecycleState.SUBMITTING)
        except Exception:
            self._reservations.pop(intent.idempotency_key, None)
            return _denial(intent, "persistence.submitting.failed")
        try:
            reference = self._transport(
                symbol=intent.symbol,
                side=intent.side.value,
                qty=float(decision.approved_quantity),
                notional=None,
                order_type=intent.order_type,
                limit_price=(
                    float(intent.limit_price)
                    if intent.limit_price is not None
                    else None
                ),
                tif=intent.tif,
                extended_hours=intent.extended_hours,
                client_order_id=intent.idempotency_key,
            )
        except Exception as exc:
            reference = None
            if self._lookup_by_client_id is not None:
                try:
                    reference = self._lookup_by_client_id(intent.idempotency_key)
                except Exception:
                    reference = None
            if reference is not None:
                self.apply_broker_event(self._event_from_reference(intent, reference))
                return OrderSubmission(decision=decision, reference=reference)
            # A definite refusal is not ambiguity. Recording it as UNKNOWN
            # left a non-terminal row the broker could never resolve, and the
            # startup reconciler reported it as missing-at-broker on every
            # later pass — permanently unhealthy, exits included.
            rejected = definite_broker_rejection(exc)
            try:
                self._append_internal(
                    self.lifecycle_store.require(intent.idempotency_key),
                    (
                        LifecycleState.REJECTED
                        if rejected
                        else LifecycleState.UNKNOWN
                    ),
                    reason=(
                        f"{type(exc).__name__}: broker refused before acceptance"
                        if rejected
                        else f"{type(exc).__name__}: transport outcome unknown"
                    ),
                )
            except Exception:
                pass
            if rejected:
                # Nothing was submitted, so the capacity this intent held is
                # free again immediately.
                self._reservations.pop(intent.idempotency_key, None)
                return OrderSubmission(
                    decision=GateDecision(
                        allowed=False,
                        approved_quantity=Decimal("0"),
                        reason_codes=(f"broker.rejected.{type(exc).__name__}",),
                        idempotency_key=intent.idempotency_key,
                    )
                )
            return OrderSubmission(
                decision=decision,
                uncertain=True,
            )
        self.apply_broker_event(self._event_from_reference(intent, reference))
        return OrderSubmission(decision=decision, reference=reference)

    enqueue = submit
    submit_intent = submit

    def apply_broker_event(self, event: BrokerOrderEvent) -> EventApplication:
        record = self.lifecycle_store.require(event.client_order_id)
        if record.terminal:
            return EventApplication(False, record, reason="terminal")
        if event.cumulative_quantity < record.cumulative_quantity:
            return EventApplication(False, record, reason="stale_cumulative")
        delta = event.cumulative_quantity - record.cumulative_quantity
        fee_delta = event.cumulative_fees - record.cumulative_fees
        if fee_delta < 0:
            return EventApplication(False, record, reason="stale_fees")
        if event.state in (LifecycleState.PARTIAL, LifecycleState.FILLED) and delta <= 0:
            return EventApplication(False, record, reason="duplicate_cumulative")
        incremental_price = None
        if delta > 0:
            if event.cumulative_average_price is None:
                return EventApplication(False, record, reason="fill_price_missing")
            previous_notional = Decimal("0")
            if record.cumulative_average_price is not None:
                previous_notional = (
                    record.cumulative_average_price * record.cumulative_quantity
                )
            incremental_notional = (
                event.cumulative_average_price * event.cumulative_quantity
                - previous_notional
            )
            if incremental_notional <= 0:
                return EventApplication(False, record, reason="fill_notional_invalid")
            incremental_price = incremental_notional / delta
        normalized = replace(
            event,
            sequence=record.version + 1,
            incremental_quantity=delta,
            incremental_price=incremental_price,
            incremental_fees=fee_delta,
        )
        try:
            result = self.lifecycle_store.append(
                normalized, expected_version=record.version
            )
        except LifecycleConflict as exc:
            current = self.lifecycle_store.require(event.client_order_id)
            return EventApplication(False, current, reason=str(exc))
        if not result.appended:
            return EventApplication(False, result.record, reason="duplicate_event")
        fill = None
        if delta > 0 and incremental_price is not None:
            notional = delta * incremental_price
            position_delta = delta if event.side is OrderSide.BUY else -delta
            cash_delta = (
                -(notional + fee_delta)
                if event.side is OrderSide.BUY
                else notional - fee_delta
            )
            fill = ConfirmedFill(
                event=normalized,
                incremental_quantity=delta,
                incremental_price=incremental_price,
                incremental_fees=fee_delta,
                position_delta=position_delta,
                cash_delta=cash_delta,
            )
            reservation = self._reservations.get(event.client_order_id)
            if reservation is not None:
                consumption = notional + fee_delta
                if (
                    reservation.side is OrderSide.BUY
                    and consumption > reservation.remaining_notional
                ):
                    self.capacity_breaches.add(event.client_order_id)
                self._reservations[event.client_order_id] = replace(
                    reservation,
                    remaining_quantity=max(
                        Decimal("0"),
                        reservation.remaining_quantity - delta,
                    ),
                    remaining_notional=max(
                        Decimal("0"),
                        reservation.remaining_notional - consumption,
                    ),
                )
            if self._confirmed_fill_handler is not None:
                self._confirmed_fill_handler(fill)
        if normalized.state in TERMINAL_LIFECYCLE_STATES:
            self._reservations.pop(event.client_order_id, None)
        if self._event_handler is not None:
            self._event_handler(normalized, fill)
        return EventApplication(True, result.record, fill=fill)
