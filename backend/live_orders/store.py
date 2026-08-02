"""Append-only, compare-and-set live order lifecycle persistence."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Optional, Protocol

from .types import (
    BrokerOrderEvent,
    LifecycleState,
    OrderIntent,
    OrderSide,
    OrderSource,
    TERMINAL_LIFECYCLE_STATES,
)


class LifecycleConflict(RuntimeError):
    """Persistence or transition truth did not match the caller's expectation."""


class LifecycleBackend(Protocol):
    def create(self, row: Mapping) -> bool: ...
    def get(self, client_order_id: str) -> Optional[dict]: ...
    def compare_and_swap(
        self, client_order_id: str, expected_version: int, row: Mapping
    ) -> bool: ...
    def list_for_instance(self, instance_id: str) -> list[dict]: ...


class InMemoryLifecycleBackend:
    """Thread-safe backend used by unit tests and non-live compatibility paths."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self._lock = threading.RLock()

    def create(self, row: Mapping) -> bool:
        cid = str(row["client_order_id"])
        with self._lock:
            if cid in self._rows:
                return False
            self._rows[cid] = copy.deepcopy(dict(row))
            return True

    def get(self, client_order_id: str) -> Optional[dict]:
        with self._lock:
            row = self._rows.get(str(client_order_id))
            return copy.deepcopy(row) if row is not None else None

    def compare_and_swap(
        self, client_order_id: str, expected_version: int, row: Mapping
    ) -> bool:
        with self._lock:
            current = self._rows.get(str(client_order_id))
            if current is None or int(current.get("version", -1)) != expected_version:
                return False
            self._rows[str(client_order_id)] = copy.deepcopy(dict(row))
            return True

    def list_for_instance(self, instance_id: str) -> list[dict]:
        with self._lock:
            return [
                copy.deepcopy(row)
                for row in self._rows.values()
                if row.get("instance_id") == str(instance_id)
            ]


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    intent: OrderIntent
    events: tuple[BrokerOrderEvent, ...]

    @property
    def client_order_id(self) -> str:
        return self.intent.idempotency_key

    @property
    def version(self) -> int:
        return self.events[-1].sequence

    @property
    def state(self) -> LifecycleState:
        return self.events[-1].state

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_LIFECYCLE_STATES

    @property
    def cumulative_quantity(self) -> Decimal:
        return self.events[-1].cumulative_quantity

    @property
    def cumulative_average_price(self) -> Optional[Decimal]:
        for event in reversed(self.events):
            if event.cumulative_average_price is not None:
                return event.cumulative_average_price
        return None

    @property
    def cumulative_fees(self) -> Decimal:
        return self.events[-1].cumulative_fees

    @property
    def broker_order_id(self) -> Optional[str]:
        for event in reversed(self.events):
            if event.broker_order_id:
                return event.broker_order_id
        return None


@dataclass(frozen=True, slots=True)
class AppendResult:
    record: LifecycleRecord
    appended: bool


def _intent_to_row(intent: OrderIntent) -> dict:
    return {
        "account_id": intent.account_id,
        "instance_id": intent.instance_id,
        "source": intent.source.value,
        "reason": intent.reason,
        "symbol": intent.symbol,
        "side": intent.side.value,
        "quantity": str(intent.quantity),
        "reduce_only": intent.reduce_only,
        "decision_at": intent.decision_at.isoformat(),
        "quote_at": intent.quote_at.isoformat(),
        "risk_snapshot_id": intent.risk_snapshot_id,
        "retry_ordinal": intent.retry_ordinal,
        "order_type": intent.order_type,
        "limit_price": (
            str(intent.limit_price) if intent.limit_price is not None else None
        ),
        "tif": intent.tif,
        "extended_hours": intent.extended_hours,
        "reference_price": (
            str(intent.reference_price)
            if intent.reference_price is not None
            else None
        ),
    }


def _intent_from_row(row: Mapping) -> OrderIntent:
    return OrderIntent(
        account_id=row["account_id"],
        instance_id=row["instance_id"],
        source=OrderSource(row["source"]),
        reason=row["reason"],
        symbol=row["symbol"],
        side=OrderSide(row["side"]),
        quantity=Decimal(row["quantity"]),
        reduce_only=bool(row["reduce_only"]),
        decision_at=datetime.fromisoformat(row["decision_at"]),
        quote_at=datetime.fromisoformat(row["quote_at"]),
        risk_snapshot_id=row["risk_snapshot_id"],
        retry_ordinal=int(row.get("retry_ordinal", 0)),
        order_type=row.get("order_type", "market"),
        limit_price=(
            Decimal(row["limit_price"]) if row.get("limit_price") is not None else None
        ),
        tif=row.get("tif", "day"),
        extended_hours=bool(row.get("extended_hours", False)),
        reference_price=(
            Decimal(row["reference_price"])
            if row.get("reference_price") is not None
            else None
        ),
    )


def _event_to_row(event: BrokerOrderEvent) -> dict:
    return {
        "event_id": event.event_id,
        "account_id": event.account_id,
        "instance_id": event.instance_id,
        "client_order_id": event.client_order_id,
        "broker_order_id": event.broker_order_id,
        "symbol": event.symbol,
        "side": event.side.value,
        "state": event.state.value,
        "cumulative_quantity": str(event.cumulative_quantity),
        "cumulative_average_price": (
            str(event.cumulative_average_price)
            if event.cumulative_average_price is not None
            else None
        ),
        "cumulative_fees": str(event.cumulative_fees),
        "occurred_at": event.occurred_at.isoformat(),
        "sequence": event.sequence,
        "incremental_quantity": str(event.incremental_quantity),
        "incremental_price": (
            str(event.incremental_price)
            if event.incremental_price is not None
            else None
        ),
        "incremental_fees": str(event.incremental_fees),
        "reason": event.reason,
    }


def _event_from_row(row: Mapping) -> BrokerOrderEvent:
    return BrokerOrderEvent(
        event_id=row["event_id"],
        account_id=row["account_id"],
        instance_id=row["instance_id"],
        client_order_id=row["client_order_id"],
        broker_order_id=row.get("broker_order_id"),
        symbol=row["symbol"],
        side=OrderSide(row["side"]),
        state=LifecycleState(row["state"]),
        cumulative_quantity=Decimal(row["cumulative_quantity"]),
        cumulative_average_price=(
            Decimal(row["cumulative_average_price"])
            if row.get("cumulative_average_price") is not None
            else None
        ),
        cumulative_fees=Decimal(row.get("cumulative_fees", "0")),
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        sequence=int(row["sequence"]),
        incremental_quantity=Decimal(row.get("incremental_quantity", "0")),
        incremental_price=(
            Decimal(row["incremental_price"])
            if row.get("incremental_price") is not None
            else None
        ),
        incremental_fees=Decimal(row.get("incremental_fees", "0")),
        reason=row.get("reason", ""),
    )


def _record_from_row(row: Mapping) -> LifecycleRecord:
    return LifecycleRecord(
        intent=_intent_from_row(row["intent"]),
        events=tuple(_event_from_row(event) for event in row["events"]),
    )


_LEGAL_TRANSITIONS = {
    LifecycleState.INTENT: frozenset(
        {
            LifecycleState.SUBMITTING,
            LifecycleState.UNKNOWN,
            LifecycleState.ACKNOWLEDGED,
            LifecycleState.PARTIAL,
            LifecycleState.FILLED,
            LifecycleState.CANCELED,
            LifecycleState.REJECTED,
            LifecycleState.EXPIRED,
        }
    ),
    LifecycleState.SUBMITTING: frozenset(
        {
            LifecycleState.SUBMITTING,
            LifecycleState.UNKNOWN,
            LifecycleState.ACKNOWLEDGED,
            LifecycleState.PARTIAL,
            LifecycleState.FILLED,
            LifecycleState.CANCELED,
            LifecycleState.REJECTED,
            LifecycleState.EXPIRED,
        }
    ),
    LifecycleState.UNKNOWN: frozenset(
        {
            LifecycleState.SUBMITTING,
            LifecycleState.UNKNOWN,
            LifecycleState.ACKNOWLEDGED,
            LifecycleState.PARTIAL,
            LifecycleState.FILLED,
            LifecycleState.CANCELED,
            LifecycleState.REJECTED,
            LifecycleState.EXPIRED,
        }
    ),
    LifecycleState.ACKNOWLEDGED: frozenset(
        {
            LifecycleState.ACKNOWLEDGED,
            LifecycleState.PARTIAL,
            LifecycleState.FILLED,
            LifecycleState.CANCELED,
            LifecycleState.REJECTED,
            LifecycleState.EXPIRED,
        }
    ),
    LifecycleState.PARTIAL: frozenset(
        {
            LifecycleState.PARTIAL,
            LifecycleState.FILLED,
            LifecycleState.CANCELED,
            LifecycleState.REJECTED,
            LifecycleState.EXPIRED,
        }
    ),
}


class OrderLifecycleStore:
    """Validated lifecycle facade over an atomic backend."""

    def __init__(self, backend: Optional[LifecycleBackend] = None) -> None:
        self.backend = backend or InMemoryLifecycleBackend()

    def get(self, client_order_id: str) -> Optional[LifecycleRecord]:
        row = self.backend.get(str(client_order_id))
        return _record_from_row(row) if row is not None else None

    def require(self, client_order_id: str) -> LifecycleRecord:
        record = self.get(client_order_id)
        if record is None:
            raise LifecycleConflict(f"unknown client order {client_order_id}")
        return record

    def create_intent(self, intent: OrderIntent) -> LifecycleRecord:
        event = BrokerOrderEvent(
            event_id=f"intent:{intent.idempotency_key}",
            account_id=intent.account_id,
            instance_id=intent.instance_id,
            client_order_id=intent.idempotency_key,
            broker_order_id=None,
            symbol=intent.symbol,
            side=intent.side,
            state=LifecycleState.INTENT,
            cumulative_quantity=Decimal("0"),
            cumulative_average_price=None,
            cumulative_fees=Decimal("0"),
            occurred_at=intent.decision_at,
            sequence=0,
        )
        row = {
            "client_order_id": intent.idempotency_key,
            "account_id": intent.account_id,
            "instance_id": intent.instance_id,
            "intent": _intent_to_row(intent),
            "events": [_event_to_row(event)],
            "version": 0,
            "state": LifecycleState.INTENT.value,
        }
        if self.backend.create(row):
            return _record_from_row(row)
        existing = self.require(intent.idempotency_key)
        # Compare identity, not every field: a re-emitted decision carries a
        # fresh risk_snapshot_id and drifted sizing while naming the same
        # order, and rejecting that as "drift" would deny the caller the
        # existing broker truth it needs to avoid posting a duplicate.
        if not existing.intent.same_identity(intent):
            raise LifecycleConflict("client-order identity drift")
        return existing

    def append(
        self, event: BrokerOrderEvent, *, expected_version: int
    ) -> AppendResult:
        record = self.require(event.client_order_id)
        for prior in record.events:
            if prior.event_id == event.event_id:
                comparable = replace(prior, sequence=event.sequence)
                if comparable != event:
                    # Raw broker events use sequence=-1 and zero increments.
                    comparable = replace(
                        comparable,
                        incremental_quantity=event.incremental_quantity,
                        incremental_price=event.incremental_price,
                        incremental_fees=event.incremental_fees,
                    )
                if (
                    prior.account_id != event.account_id
                    or prior.instance_id != event.instance_id
                    or prior.client_order_id != event.client_order_id
                    or prior.symbol != event.symbol
                    or prior.side != event.side
                    or prior.state != event.state
                    or prior.cumulative_quantity != event.cumulative_quantity
                ):
                    raise LifecycleConflict("duplicate event identity drift")
                return AppendResult(record=record, appended=False)
        if record.version != expected_version:
            raise LifecycleConflict(
                f"expected version {expected_version}, found {record.version}"
            )
        if record.terminal:
            raise LifecycleConflict(f"terminal lifecycle cannot be rewritten: {record.state.value}")
        if (
            event.account_id != record.intent.account_id
            or event.instance_id != record.intent.instance_id
            or event.client_order_id != record.intent.idempotency_key
            or event.symbol != record.intent.symbol
            or event.side != record.intent.side
        ):
            raise LifecycleConflict("lifecycle identity drift")
        known_broker_id = record.broker_order_id
        if known_broker_id and event.broker_order_id and event.broker_order_id != known_broker_id:
            raise LifecycleConflict("broker-order identity drift")
        if event.cumulative_quantity < record.cumulative_quantity:
            raise LifecycleConflict("cumulative quantity moved backwards")
        allowed = _LEGAL_TRANSITIONS.get(record.state, frozenset())
        if event.state not in allowed:
            raise LifecycleConflict(
                f"illegal transition {record.state.value}->{event.state.value}"
            )
        normalized = replace(
            event,
            broker_order_id=event.broker_order_id or known_broker_id,
            sequence=record.version + 1,
        )
        current_row = self.backend.get(event.client_order_id)
        if current_row is None:
            raise LifecycleConflict("lifecycle disappeared during append")
        next_row = copy.deepcopy(current_row)
        next_row["events"].append(_event_to_row(normalized))
        next_row["version"] = normalized.sequence
        next_row["state"] = normalized.state.value
        if not self.backend.compare_and_swap(
            event.client_order_id, record.version, next_row
        ):
            raced = self.require(event.client_order_id)
            if any(item.event_id == event.event_id for item in raced.events):
                return AppendResult(record=raced, appended=False)
            raise LifecycleConflict(
                f"expected version {expected_version}, found {raced.version}"
            )
        return AppendResult(record=_record_from_row(next_row), appended=True)

    def list_for_instance(self, instance_id: str) -> tuple[LifecycleRecord, ...]:
        return tuple(
            _record_from_row(row)
            for row in self.backend.list_for_instance(str(instance_id))
        )
