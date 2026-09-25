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
    OrderSource,
    TERMINAL_LIFECYCLE_STATES,
)


#: Sources whose rows RECORD something the broker already did (a bracket's
#: child leg, an option assignment). They are never submitted: posting one
#: would send an order nobody decided.
_RECORD_ONLY_SOURCES = frozenset(
    {OrderSource.BRACKET_LEG, OrderSource.OPTION_ACTIVITY}
)

#: An Alpaca bracket parent carries exactly two child legs, the take-profit
#: limit and the stop-loss stop. ensure_bracket_legs retries a parent until
#: both are recorded.
_BRACKET_LEG_COUNT = 2


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
    if terminal_intent.broker_client_order_id is not None:
        # A broker-keyed row (bracket leg, option activity) is keyed by an id
        # the broker minted: a "retry" would reuse that key under a new
        # identity payload and surface as identity drift. It has no retry.
        raise TerminalRetryExhausted(
            "a broker-keyed intent cannot be retried: the broker minted its "
            "client order id"
        )
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


def _transport_extras(intent: OrderIntent) -> dict:
    """Transport keywords only a new-field intent carries. {} for every EB
    intent, so its transport call is byte-identical (pinned in
    tests/test_swing_live_service.py)."""
    extras: dict = {}
    if intent.order_class is not None:
        extras["order_class"] = intent.order_class
        extras["take_profit"] = float(intent.take_profit_price)
        extras["stop_loss"] = float(intent.stop_loss_price)
    if intent.asset_class != "us_equity":
        extras["asset_class"] = intent.asset_class
    if intent.position_intent is not None:
        extras["position_intent"] = intent.position_intent
    return extras


def bracket_leg_intent(parent: OrderIntent, leg) -> OrderIntent:
    """The lifecycle intent for one child leg of a bracket parent.

    Keyed by the client order id Alpaca minted for the leg, so a leg fill,
    from the stream or from reconciliation, resolves to this row. The
    take-profit leg is a limit order; the stop-loss leg is recorded as a
    market exit that carries its stop price.
    """
    leg_cid = str(getattr(leg, "client_order_id", "") or "").strip()
    if not leg_cid:
        raise ValueError("a bracket leg without a client order id cannot be tracked")
    leg_type = str(
        getattr(getattr(leg, "order_type", None), "value", getattr(leg, "order_type", None))
        or ""
    ).strip().lower()
    take_profit = leg_type == "limit"
    quantity = Decimal(str(getattr(leg, "qty", 0) or 0))
    if quantity <= 0:
        quantity = parent.quantity
    limit_price = None
    if take_profit:
        raw_limit = getattr(leg, "limit_price", None)
        limit_price = (
            Decimal(str(raw_limit))
            if raw_limit not in (None, 0, "0")
            else parent.take_profit_price
        )
    return OrderIntent(
        account_id=parent.account_id,
        instance_id=parent.instance_id,
        source=OrderSource.BRACKET_LEG,
        reason="bracket_take_profit" if take_profit else "bracket_stop_loss",
        symbol=parent.symbol,
        side=OrderSide.SELL,
        quantity=quantity,
        reduce_only=True,
        decision_at=parent.decision_at,
        quote_at=parent.quote_at,
        risk_snapshot_id=parent.risk_snapshot_id,
        order_type="limit" if take_profit else "market",
        limit_price=limit_price,
        tif="gtc",
        take_profit_price=parent.take_profit_price,
        stop_loss_price=parent.stop_loss_price,
        parent_client_order_id=parent.idempotency_key,
        broker_client_order_id=leg_cid,
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
        legs_lookup: Optional[Callable[[str], Any]] = None,
        confirmed_fill_handler: Optional[Callable[[ConfirmedFill], None]] = None,
        event_handler: Optional[
            Callable[[BrokerOrderEvent, Optional[ConfirmedFill]], None]
        ] = None,
        max_terminal_retries: int = 2,
        log: Optional[Callable[..., None]] = None,
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
        self._legs_lookup = legs_lookup
        self._confirmed_fill_handler = confirmed_fill_handler
        self._event_handler = event_handler
        self.max_terminal_retries = int(max_terminal_retries)
        self._reservations: dict[str, Reservation] = {}
        self.capacity_breaches: set[str] = set()
        self._log = log
        #: Identities already reported through `_report_swallowed`. A retried
        #: exit must not fill the log with the same line, and a set is the
        #: whole of the throttle.
        self._reported_failures: set[str] = set()
        self._restore_reservations()

    def _report_swallowed(self, intent, reason: str, exc: BaseException) -> None:
        """Say what an `except Exception` swallowed, once per identity.

        `submit` turns five different exceptions into reason codes —
        `dependency.snapshot.unavailable`, `persistence.intent.failed` and the
        rest — and discarded the exception itself. The code names the STAGE
        that failed; without this nothing anywhere named the cause, so a live
        exit that never reached the broker left one opaque token behind.
        """
        if self._log is None:
            return
        key = f"{getattr(intent, 'idempotency_key', '')}:{reason}"
        if key in self._reported_failures:
            return
        self._reported_failures.add(key)
        try:
            self._log(
                f"live-order {reason}: {getattr(intent, 'side', '')} "
                f"{getattr(intent, 'symbol', '')} "
                f"({getattr(intent, 'idempotency_key', '')}) — "
                f"{type(exc).__name__}: {exc}",
                "red")
        except Exception:
            pass

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
            if intent.contract_multiplier != 1:
                # swing-port: the same x100 submit reserved and fills consume
                # (a contract moves quantity x price x multiplier; fees are
                # already dollars).
                reference = reference * intent.contract_multiplier
                if (
                    record.cumulative_average_price is not None
                    and record.cumulative_quantity > 0
                ):
                    consumed = (
                        record.cumulative_average_price
                        * record.cumulative_quantity
                        * intent.contract_multiplier
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
        except Exception as exc:
            self._report_swallowed(intent, "broker.reconciliation.unavailable", exc)
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
        if intent.source in _RECORD_ONLY_SOURCES:
            # L1 review: a record-only row never reaches the gate or the
            # broker -- and a retry of one is refused here, before identity,
            # so it can never surface as idempotency.identity_drift.
            return _denial(intent, "authorization.record_only_source")
        intent, existing, denial = self._live_identity(intent)
        if denial:
            return _denial(intent, denial)
        if existing is not None:
            return self._reconcile_existing(intent, existing)
        try:
            snapshot = self._snapshot_provider(intent)
        except Exception as exc:
            self._report_swallowed(intent, "dependency.snapshot.unavailable", exc)
            return _denial(intent, "dependency.snapshot.unavailable")
        if not isinstance(snapshot, DependencySnapshot):
            self._report_swallowed(
                intent, "dependency.snapshot.invalid",
                TypeError(f"snapshot provider returned {type(snapshot).__name__}"))
            return _denial(intent, "dependency.snapshot.invalid")
        decision = self._gate.evaluate(intent, snapshot)
        if not decision.allowed:
            return OrderSubmission(decision=decision)
        try:
            record = self.lifecycle_store.create_intent(intent)
        except Exception as exc:
            self._report_swallowed(intent, "persistence.intent.failed", exc)
            return _denial(intent, "persistence.intent.failed")
        reserved_notional = decision.approved_quantity * snapshot.quote_price
        if intent.contract_multiplier != 1:
            reserved_notional = reserved_notional * intent.contract_multiplier
        self._reservations[intent.idempotency_key] = Reservation(
            client_order_id=intent.idempotency_key,
            side=intent.side,
            remaining_quantity=decision.approved_quantity,
            remaining_notional=reserved_notional,
        )
        try:
            record = self._append_internal(record, LifecycleState.SUBMITTING)
        except Exception as exc:
            self._report_swallowed(intent, "persistence.submitting.failed", exc)
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
                **_transport_extras(intent),
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
                self._register_legs_quietly(intent, reference)
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
        self._register_legs_quietly(intent, reference)
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
            multiplier = record.intent.contract_multiplier
            if multiplier != 1:
                # swing-port: a contract fill moves quantity x price x 100.
                notional = notional * multiplier
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
                asset_class=record.intent.asset_class,
                contract_multiplier=multiplier,
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

    def _register_legs_quietly(self, intent: OrderIntent, reference: Any) -> None:
        """Bracket parents only: read the legs back and record them. A failure
        never turns an accepted order into a refusal; ensure_bracket_legs
        retries it on the next reconcile."""
        if intent.order_class != "bracket" or self._legs_lookup is None:
            return
        try:
            self.register_bracket_legs(intent, reference)
        except Exception as exc:
            self._report_swallowed(intent, "bracket.legs.unregistered", exc)

    def register_bracket_legs(
        self, parent_intent: OrderIntent, parent_reference: Any
    ) -> tuple[LifecycleRecord, ...]:
        """Record each child leg of a submitted bracket as its own lifecycle
        row (source bracket_leg, keyed by Alpaca's leg client order id), so a
        leg fill resolves to a known record (spec section 6.1)."""
        broker_order_id = str(
            getattr(parent_reference, "broker_order_id", None)
            or getattr(parent_reference, "id", None)
            or ""
        ).strip()
        if not broker_order_id:
            raise LifecycleConflict(
                "bracket parent has no broker order id; legs cannot be read"
            )
        return self._register_legs_for(parent_intent, broker_order_id)

    def _register_legs_for(
        self, parent_intent: OrderIntent, broker_order_id: str
    ) -> tuple[LifecycleRecord, ...]:
        if self._legs_lookup is None or parent_intent.order_class != "bracket":
            return ()
        nested = self._legs_lookup(broker_order_id)
        records = []
        for leg in tuple(getattr(nested, "legs", ()) or ()):
            leg_intent = bracket_leg_intent(parent_intent, leg)
            if self.lifecycle_store.get(leg_intent.idempotency_key) is None:
                self.lifecycle_store.create_intent(leg_intent)
            self.apply_broker_event(self._event_from_reference(leg_intent, leg))
            records.append(self.lifecycle_store.require(leg_intent.idempotency_key))
        return tuple(records)

    def ensure_bracket_legs(self) -> int:
        """Register the legs of every bracket parent that has fewer than both
        recorded: a nested read that failed right after submission, a second
        leg that failed after the first was stored, or a restart. Returns the
        number of leg rows newly registered. Idempotent."""
        if self._legs_lookup is None:
            return 0
        records = self.lifecycle_store.list_for_instance(self.instance_id)
        legs_of: dict[str, set[str]] = {}
        for record in records:
            if record.intent.source is OrderSource.BRACKET_LEG:
                legs_of.setdefault(
                    record.intent.parent_client_order_id, set()
                ).add(record.client_order_id)
        registered = 0
        for record in records:
            intent = record.intent
            if intent.order_class != "bracket":
                continue
            known = legs_of.get(record.client_order_id, set())
            # L3 carry: a parent with ONE leg recorded is incomplete too; its
            # other leg (take-profit or stop-loss) would never resolve a fill.
            if len(known) >= _BRACKET_LEG_COUNT or not record.broker_order_id:
                continue
            if record.terminal and record.cumulative_quantity <= 0:
                continue
            try:
                rows = self._register_legs_for(intent, record.broker_order_id)
                registered += sum(
                    1 for row in rows if row.client_order_id not in known
                )
            except Exception as exc:
                self._report_swallowed(intent, "bracket.legs.unregistered", exc)
        return registered

    def record_external_fill(
        self,
        intent: OrderIntent,
        *,
        broker_order_id: str,
        quantity,
        price,
        occurred_at: datetime,
        reason: str = "",
    ) -> EventApplication:
        """Record a fill no order produced (an option assignment) as a FILLED
        lifecycle row. Exactly once: a second call finds the row terminal and
        applies nothing, so no fill is counted or announced twice."""
        if self.lifecycle_store.get(intent.idempotency_key) is None:
            self.lifecycle_store.create_intent(intent)
        quantity = Decimal(str(quantity))
        price = Decimal(str(price))
        filled = BrokerOrderEvent(
            event_id=_event_id(
                intent.idempotency_key,
                broker_order_id,
                LifecycleState.FILLED,
                quantity,
                Decimal("0"),
            ),
            account_id=intent.account_id,
            instance_id=intent.instance_id,
            client_order_id=intent.idempotency_key,
            broker_order_id=broker_order_id,
            symbol=intent.symbol,
            side=intent.side,
            state=LifecycleState.FILLED,
            cumulative_quantity=quantity,
            cumulative_average_price=price,
            cumulative_fees=Decimal("0"),
            occurred_at=occurred_at,
            reason=reason,
        )
        return self.apply_broker_event(filled)
