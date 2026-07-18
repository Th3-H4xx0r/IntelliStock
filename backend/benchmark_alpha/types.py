"""Typed contracts for the benchmark-alpha system (Task 5; Task 7 extends).

The central type-safety rule (adversarial finding E01): the execution
scheduler tick (``FULL``/``MONITOR``/``IDLE``) and the brokerage execution
mode (``OFF``/``OBSERVE``/``SHADOW_PORTFOLIO``/``PAPER``/``LIVE``) are
different typed fields. A scheduler tick can NEVER be interpreted as an
execution mode — that collision silently disabled live safeguards in the
legacy daemon.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# Absolute ceiling on active (non-SPY, non-cash) exposure. Promotion tiers
# authorize at most this much; nothing can raise it.
ABSOLUTE_ACTIVE_CEILING = 0.80


class RunOrigin(Enum):
    BACKTEST = "BACKTEST"
    OBSERVE = "OBSERVE"
    SHADOW_PORTFOLIO = "SHADOW_PORTFOLIO"
    PAPER = "PAPER"
    LIVE = "LIVE"


class ExecutionMode(Enum):
    OFF = "OFF"
    OBSERVE = "OBSERVE"
    SHADOW_PORTFOLIO = "SHADOW_PORTFOLIO"
    PAPER = "PAPER"
    LIVE = "LIVE"


class SchedulerTickMode(Enum):
    FULL = "FULL"
    MONITOR = "MONITOR"
    IDLE = "IDLE"


class PromotionTier(Enum):
    OBSERVE = "OBSERVE"
    SHADOW_PORTFOLIO = "SHADOW_PORTFOLIO"
    PAPER = "PAPER"
    LIVE_40 = "LIVE_40"
    LIVE_60 = "LIVE_60"
    LIVE_80 = "LIVE_80"


class RunPhase(Enum):
    FORECASTED = "FORECASTED"
    ALLOCATED = "ALLOCATED"
    INTENTS_WRITTEN = "INTENTS_WRITTEN"
    REDUCTIONS_SUBMITTED = "REDUCTIONS_SUBMITTED"
    REDUCTIONS_SETTLED = "REDUCTIONS_SETTLED"
    INCREASES_SUBMITTED = "INCREASES_SUBMITTED"
    SETTLED_OR_EXPIRED = "SETTLED_OR_EXPIRED"


class EventKind(Enum):
    PREDICTION = "PREDICTION"
    DECISION = "DECISION"
    GATE = "GATE"
    ALLOCATION = "ALLOCATION"
    ORDER_INTENT = "ORDER_INTENT"
    BROKER_ORDER = "BROKER_ORDER"
    FILL = "FILL"
    CASH_ACTIVITY = "CASH_ACTIVITY"
    PORTFOLIO_SNAPSHOT = "PORTFOLIO_SNAPSHOT"
    OUTCOME = "OUTCOME"
    RISK = "RISK"
    TAX = "TAX"
    INCIDENT = "INCIDENT"
    DEMOTION = "DEMOTION"


class EvidenceClass(Enum):
    DIRECT = "DIRECT"
    PROPAGATION = "PROPAGATION"
    DETERMINISTIC = "DETERMINISTIC"


class GateEffect(Enum):
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    HELD = "HELD"


class OrderEffect(Enum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OwnerSource(Enum):
    """Resolved order ownership. There is deliberately no UNKNOWN member —
    unresolved ownership is a validation error and blocks promotion."""
    STRATEGY = "STRATEGY"
    DASHBOARD = "DASHBOARD"
    EXTERNAL = "EXTERNAL"


def require_execution_mode(value):
    """Return ``value`` if it is an ``ExecutionMode``; raise ``TypeError``
    otherwise — including for ``SchedulerTickMode`` members and raw strings.
    Every consumer that decides whether live safeguards apply must pass
    through this guard (E01)."""
    if isinstance(value, ExecutionMode):
        return value
    raise TypeError(
        f"expected ExecutionMode, got {type(value).__name__} {value!r} — "
        "a scheduler tick (FULL/MONITOR/IDLE) is never an execution mode")


def live_invariants_expected(execution_mode, scheduler_tick):
    """True when live safeguards must be active for this run event.

    LIVE execution demands live invariants on EVERY scheduler tick —
    FULL, MONITOR, and IDLE alike.
    """
    require_execution_mode(execution_mode)
    if not isinstance(scheduler_tick, SchedulerTickMode):
        raise TypeError(
            f"expected SchedulerTickMode, got {type(scheduler_tick).__name__}")
    return execution_mode is ExecutionMode.LIVE


@dataclass(frozen=True)
class AuthorizationContext:
    """Immutable promotion-tier authorization.

    Missing, expired, or malformed authorization permits no exposure
    increase — resolve through ``authorized_active_cap``.
    """
    tier: PromotionTier
    active_cap: float
    evidence_allowlist: tuple
    expires_at: datetime
    artifact_hashes: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.tier, PromotionTier):
            raise ValueError("tier must be a PromotionTier")
        cap = self.active_cap
        if not isinstance(cap, (int, float)) or isinstance(cap, bool) \
                or not (0.0 <= float(cap) <= ABSOLUTE_ACTIVE_CEILING):
            raise ValueError(
                f"active_cap must be within [0, {ABSOLUTE_ACTIVE_CEILING}], got {cap!r}")
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be a timezone-aware datetime")
        object.__setattr__(self, "evidence_allowlist",
                           tuple(self.evidence_allowlist or ()))

    def is_active(self, now):
        return now < self.expires_at


def authorized_active_cap(authorization, now):
    """Resolve the active-exposure cap granted by ``authorization`` at ``now``.

    ``None``, expired, or malformed authorization yields 0.0 — no increase.
    """
    if authorization is None or not isinstance(authorization, AuthorizationContext):
        return 0.0
    if not authorization.is_active(now):
        return 0.0
    return float(authorization.active_cap)
