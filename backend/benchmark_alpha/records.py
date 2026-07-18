"""Immutable typed alpha records with per-record natural identities (Task 7).

Every record hashes ``schema_version`` + record type + its complete natural
identity — a generic run/type/symbol/time formula is prohibited (H13).
Records serialize via ``to_doc()`` with enums as values and timestamps as
ISO strings; unknown enum values, naive timestamps, non-finite numbers, and
unresolved ownership are rejected at construction.
"""
import hashlib
import json
import math
from dataclasses import dataclass, fields as dc_fields
from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional

from benchmark_alpha.types import (
    EvidenceClass,
    GateEffect,
    OrderEffect,
    OwnerSource,
    RunOrigin,
)

SCHEMA_VERSION = 1


def _record_id_for(record_type, identity):
    canonical = json.dumps(
        {"schema_version": SCHEMA_VERSION, "record_type": record_type,
         "identity": identity},
        sort_keys=True, separators=(",", ":"), default=str)
    return f"{record_type}-{hashlib.sha256(canonical.encode()).hexdigest()[:32]}"


def _aware(name, value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _finite(name, value, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{name} is required")
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _enum(name, value, enum_cls):
    if not isinstance(value, enum_cls):
        raise ValueError(f"{name} must be a {enum_cls.__name__}, got {value!r}")
    return value


def _ser(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_ser(v) for v in value]
    if isinstance(value, dict):
        return {k: _ser(v) for k, v in value.items()}
    if hasattr(value, "to_doc"):
        return value.to_doc()
    return value


class _AlphaRecord:
    """Mixin: ``to_doc`` from dataclass fields + hashed natural identity."""

    def _identity(self):  # pragma: no cover - overridden
        raise NotImplementedError

    @property
    def record_id(self):
        return _record_id_for(self.RECORD_TYPE, self._identity())

    def to_doc(self):
        doc = {"id": self.record_id, "schema_version": SCHEMA_VERSION,
               "record_type": self.RECORD_TYPE}
        for f in dc_fields(self):
            doc[f.name] = _ser(getattr(self, f.name))
        return doc


@dataclass(frozen=True)
class Forecast(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaPredictions"
    RECORD_TYPE: ClassVar[str] = "forecast"

    producer: str
    model_version: str
    evidence_class: EvidenceClass
    symbol: str
    as_of: datetime
    horizon_trading_days: int
    expected_excess_return: float
    probability_outperform: float
    confidence: float
    feature_version: str
    data_snapshot_id: str
    eligibility: bool
    gate_reasons: tuple
    revision: int
    instance_id: str
    origin: RunOrigin
    run_id: str

    def __post_init__(self):
        _enum("evidence_class", self.evidence_class, EvidenceClass)
        _enum("origin", self.origin, RunOrigin)
        _aware("as_of", self.as_of)
        _finite("expected_excess_return", self.expected_excess_return)
        for name in ("probability_outperform", "confidence"):
            v = _finite(name, getattr(self, name))
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be within [0,1]")
        if int(self.horizon_trading_days) <= 0:
            raise ValueError("horizon_trading_days must be positive")
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "gate_reasons", tuple(self.gate_reasons or ()))

    def _identity(self):
        return {"producer": self.producer, "model_version": self.model_version,
                "evidence_class": self.evidence_class.value,
                "symbol": self.symbol, "as_of": self.as_of.isoformat(),
                "horizon_trading_days": int(self.horizon_trading_days),
                "revision": int(self.revision)}


@dataclass(frozen=True)
class GateRecord(_AlphaRecord):
    """The decision record: every buy/sell/hold/skip/rejection/override is one
    of these — an order is not required for a decision to be auditable."""
    TABLE: ClassVar[str] = "AlphaGates"
    RECORD_TYPE: ClassVar[str] = "decision"

    run_id: str
    instance_id: str
    origin: RunOrigin
    symbol: str
    as_of: datetime
    effect: GateEffect
    reason_codes: tuple
    forecast_id: Optional[str]
    revision: int

    def __post_init__(self):
        _enum("effect", self.effect, GateEffect)
        _enum("origin", self.origin, RunOrigin)
        _aware("as_of", self.as_of)
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes or ()))

    def _identity(self):
        return {"run_id": self.run_id, "symbol": self.symbol,
                "as_of": self.as_of.isoformat(), "revision": int(self.revision)}


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    weight: float
    forecast_id: Optional[str]
    reason_codes: tuple = ()

    def __post_init__(self):
        w = _finite("weight", self.weight)
        if not 0.0 <= w <= 1.0:
            raise ValueError("weight must be within [0,1]")
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes or ()))

    def to_doc(self):
        return {"symbol": self.symbol, "weight": self.weight,
                "forecast_id": self.forecast_id,
                "reason_codes": list(self.reason_codes)}



@dataclass(frozen=True)
class AllocationPlan(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaAllocations"
    RECORD_TYPE: ClassVar[str] = "allocation"

    run_id: str
    instance_id: str
    origin: RunOrigin
    as_of: datetime
    revision: int
    targets: tuple
    reason_codes: tuple = ()

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _aware("as_of", self.as_of)
        targets = tuple(self.targets or ())
        for t in targets:
            if not isinstance(t, TargetPosition):
                raise ValueError("targets must be TargetPosition instances")
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes or ()))

    def _identity(self):
        return {"run_id": self.run_id, "as_of": self.as_of.isoformat(),
                "revision": int(self.revision)}


@dataclass(frozen=True)
class OrderIntent(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaOrderIntents"
    RECORD_TYPE: ClassVar[str] = "intent"

    run_id: str
    allocation_id: str
    decision_id: str
    forecast_id: Optional[str]
    instance_id: str
    origin: RunOrigin
    symbol: str
    side: str
    qty: Optional[float]
    notional: Optional[float]
    reserved_cash: float
    mark_id: str
    mark_source: str
    mark_age_seconds: float
    owner_source: OwnerSource
    config_hash: str
    reason_codes: tuple
    as_of: datetime
    revision: int

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _enum("owner_source", self.owner_source, OwnerSource)
        _aware("as_of", self.as_of)
        if str(self.side).lower() not in ("buy", "sell"):
            raise ValueError(f"side must be buy/sell, got {self.side!r}")
        if self.qty is None and self.notional is None:
            raise ValueError("one of qty/notional is required")
        _finite("qty", self.qty, allow_none=True)
        _finite("notional", self.notional, allow_none=True)
        _finite("reserved_cash", self.reserved_cash)
        _finite("mark_age_seconds", self.mark_age_seconds)
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "side", str(self.side).lower())
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes or ()))

    def _identity(self):
        return {"allocation_id": self.allocation_id, "symbol": self.symbol,
                "side": self.side, "revision": int(self.revision)}

    @property
    def intent_id(self):
        return self.record_id

    @property
    def client_order_id(self):
        # Stable intent identity + revision — never date/symbol/side alone.
        return f"alpha-{self.instance_id}-{self.record_id[-20:]}"

    def to_doc(self):
        doc = super().to_doc()
        doc["intent_id"] = self.intent_id
        doc["client_order_id"] = self.client_order_id
        return doc


@dataclass(frozen=True)
class BrokerOrderRecord(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaBrokerOrders"
    RECORD_TYPE: ClassVar[str] = "broker_order"

    intent_id: str
    intent_revision: int
    broker_order_id: str
    instance_id: str
    origin: RunOrigin
    symbol: str
    side: str
    effect: OrderEffect
    requested_qty: float
    as_of: datetime
    owner_source: OwnerSource

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _enum("effect", self.effect, OrderEffect)
        _enum("owner_source", self.owner_source, OwnerSource)
        _aware("as_of", self.as_of)
        _finite("requested_qty", self.requested_qty)
        object.__setattr__(self, "symbol", str(self.symbol).upper())

    def _identity(self):
        return {"intent_id": self.intent_id,
                "intent_revision": int(self.intent_revision),
                "broker_order_id": self.broker_order_id,
                "effect": self.effect.value}


@dataclass(frozen=True)
class FillRecord(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaFills"
    RECORD_TYPE: ClassVar[str] = "fill"

    broker_order_id: str
    activity_id: Optional[str]
    instance_id: str
    origin: RunOrigin
    symbol: str
    side: str
    cumulative_qty: float
    delta_qty: float
    price: float
    event_time: datetime
    sequence: int
    owner_source: OwnerSource

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _enum("owner_source", self.owner_source, OwnerSource)
        _aware("event_time", self.event_time)
        cumulative = _finite("cumulative_qty", self.cumulative_qty)
        delta = _finite("delta_qty", self.delta_qty)
        _finite("price", self.price)
        if delta <= 0:
            raise ValueError("delta_qty must be positive (monotonic fills)")
        if delta > cumulative:
            raise ValueError(
                "delta_qty exceeds cumulative_qty — partial-fill regression")
        object.__setattr__(self, "symbol", str(self.symbol).upper())

    def _identity(self):
        if self.activity_id:
            return {"activity_id": self.activity_id}
        return {"broker_order_id": self.broker_order_id,
                "cumulative_qty": self.cumulative_qty,
                "event_time": self.event_time.isoformat(),
                "sequence": int(self.sequence)}


@dataclass(frozen=True)
class PortfolioSnapshot(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaPortfolioSnapshots"
    RECORD_TYPE: ClassVar[str] = "portfolio_snapshot"

    instance_id: str
    origin: RunOrigin
    as_of: datetime
    equity: float
    cash: float
    positions: dict
    run_id: Optional[str] = None

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _aware("as_of", self.as_of)
        _finite("equity", self.equity)
        _finite("cash", self.cash)

    def _identity(self):
        return {"instance_id": self.instance_id, "origin": self.origin.value,
                "as_of": self.as_of.isoformat(), "run_id": self.run_id}


@dataclass(frozen=True)
class CashActivity(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaCashActivities"
    RECORD_TYPE: ClassVar[str] = "cash_activity"

    activity_id: str
    activity_type: str
    amount: float
    effective_at: datetime
    instance_id: str
    origin: RunOrigin

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _aware("effective_at", self.effective_at)
        _finite("amount", self.amount)
        if not self.activity_id:
            raise ValueError("activity_id (broker authoritative) is required")

    def _identity(self):
        return {"activity_id": self.activity_id}


@dataclass(frozen=True)
class HorizonOutcome(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaOutcomes"
    RECORD_TYPE: ClassVar[str] = "outcome"

    forecast_id: str
    horizon_trading_days: int
    data_revision: int
    instance_id: str
    origin: RunOrigin
    entry_session: str
    exit_session: Optional[str]
    entry_price_raw: Optional[float]
    exit_price_raw: Optional[float]
    entry_price_adjusted: Optional[float]
    exit_price_adjusted: Optional[float]
    spy_entry_adjusted: Optional[float]
    spy_exit_adjusted: Optional[float]
    benchmark_relative_return: Optional[float]
    corporate_action_state: str
    missing_reason: Optional[str]
    hypothesis_generating: bool
    evaluated_at: datetime

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _aware("evaluated_at", self.evaluated_at)
        if int(self.horizon_trading_days) <= 0:
            raise ValueError("horizon_trading_days must be positive")
        if self.missing_reason is None:
            for name in ("entry_price_adjusted", "exit_price_adjusted",
                         "spy_entry_adjusted", "spy_exit_adjusted",
                         "benchmark_relative_return"):
                _finite(name, getattr(self, name))
        else:
            # Conservative missing outcome stays in the denominator.
            for name in ("entry_price_raw", "exit_price_raw",
                         "entry_price_adjusted", "exit_price_adjusted",
                         "spy_entry_adjusted", "spy_exit_adjusted",
                         "benchmark_relative_return"):
                _finite(name, getattr(self, name), allow_none=True)

    def _identity(self):
        return {"forecast_id": self.forecast_id,
                "horizon_trading_days": int(self.horizon_trading_days),
                "data_revision": int(self.data_revision)}


@dataclass(frozen=True)
class IncidentRecord(_AlphaRecord):
    TABLE: ClassVar[str] = "AlphaIncidents"
    RECORD_TYPE: ClassVar[str] = "incident"

    instance_id: str
    opened_at: datetime
    kind: str
    severity: str
    description: str
    origin: RunOrigin

    def __post_init__(self):
        _enum("origin", self.origin, RunOrigin)
        _aware("opened_at", self.opened_at)
        if self.severity not in ("info", "warning", "critical"):
            raise ValueError(f"unknown severity {self.severity!r}")

    def _identity(self):
        return {"instance_id": self.instance_id,
                "opened_at": self.opened_at.isoformat(), "kind": self.kind}
