"""Timestamped market-mark contract (Task 3, benchmark-alpha Stage A).

Entry cost, fill price, and current market mark are different concepts. Every
mark carries observation/receive timestamps, source, feed, and quality so that
consumers can enforce purpose-specific freshness policies instead of trusting
an untimestamped scalar cache (the July 10 stale-fill-price incident).

Precedence: a mark with an older ``observed_at`` can never replace a newer
mark. At the same timestamp higher-priority sources win — quotes and trades
outrank broker-position marks, which outrank fills; fill marks never outrank
anything else.
"""
import math
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo


class MarkSource(Enum):
    STREAM_QUOTE = "stream_quote"
    STREAM_TRADE = "stream_trade"
    REST_QUOTE = "rest_quote"
    BROKER_POSITION = "broker_position"
    FILL = "fill"


class MarkQuality(Enum):
    CONSOLIDATED = "consolidated"
    SINGLE_EXCHANGE = "single_exchange"
    BROKER_DERIVED = "broker_derived"
    EXECUTION_ONLY = "execution_only"


class MarkPurpose(Enum):
    DECISION = "decision"
    SUBMISSION = "submission"
    RISK_REDUCTION = "risk_reduction"


# Same-timestamp precedence. Fill marks are execution records, not market
# truth, so they carry the lowest rank and can be replaced by anything.
_SOURCE_PRIORITY = {
    MarkSource.FILL: 0,
    MarkSource.BROKER_POSITION: 1,
    MarkSource.REST_QUOTE: 2,
    MarkSource.STREAM_TRADE: 3,
    MarkSource.STREAM_QUOTE: 4,
}

_HALT_CONDITIONS = frozenset({"LULD", "HALT", "HALTED", "T1", "T2", "T5"})


def _require_finite_positive(name, value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")


def _require_aware(name, value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


@dataclass(frozen=True)
class MarketMark:
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    observed_at: datetime
    received_at: datetime
    source: MarkSource
    feed: str
    quality: MarkQuality
    session: str
    conditions: tuple = ()
    halted: bool = False

    def __post_init__(self):
        _require_finite_positive("price", self.price)
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None:
                _require_finite_positive(name, value)
        for name in ("bid_size", "ask_size"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite non-negative number")
        _require_aware("observed_at", self.observed_at)
        _require_aware("received_at", self.received_at)
        if not isinstance(self.source, MarkSource):
            raise ValueError("source must be a MarkSource")
        if not isinstance(self.quality, MarkQuality):
            raise ValueError("quality must be a MarkQuality")
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "conditions", tuple(self.conditions or ()))

    def age_seconds(self, now):
        return (now - self.observed_at).total_seconds()

    def clock_skew_seconds(self):
        return abs((self.received_at - self.observed_at).total_seconds())

    def is_crossed_or_locked(self):
        if self.bid is None or self.ask is None:
            return False
        return self.bid >= self.ask

    def has_empty_side(self):
        return any(size == 0 for size in (self.bid_size, self.ask_size)
                   if size is not None)

    def halt_signaled(self):
        return self.halted or any(
            str(c).upper() in _HALT_CONDITIONS for c in self.conditions)


class MarketMarkBook:
    """Thread-safe per-symbol newest-mark store with source precedence."""

    def __init__(self):
        self._marks = {}
        self._lock = threading.RLock()

    def update(self, mark):
        if not isinstance(mark, MarketMark):
            raise ValueError("update requires a MarketMark")
        with self._lock:
            current = self._marks.get(mark.symbol)
            if current is not None:
                if mark.observed_at < current.observed_at:
                    return False
                if mark.observed_at == current.observed_at and (
                        _SOURCE_PRIORITY[mark.source]
                        <= _SOURCE_PRIORITY[current.source]):
                    return False
            self._marks[mark.symbol] = mark
            return True

    def get(self, symbol):
        with self._lock:
            return self._marks.get(str(symbol).upper())

    def snapshot(self):
        with self._lock:
            return dict(self._marks)

    def fresh_price(self, symbol, now, max_age_seconds, allowed_qualities=None):
        mark = self.get(symbol)
        if mark is None:
            return None
        if mark.age_seconds(now) > max_age_seconds:
            return None
        if allowed_qualities is not None and mark.quality not in set(allowed_qualities):
            return None
        return mark.price


@dataclass(frozen=True)
class PurposePolicy:
    """Explicit per-purpose source/age/spread/skew/halt policy."""
    primary_sources: frozenset
    primary_max_age_seconds: float
    fallback_sources: frozenset
    fallback_max_age_seconds: float
    forbidden_sources: frozenset
    max_clock_skew_seconds: float
    allow_halted: bool
    reject_invalid_quotes: bool
    degraded_age_seconds: float = 60.0


_INCREASE_SOURCES = frozenset({
    MarkSource.STREAM_QUOTE, MarkSource.STREAM_TRADE, MarkSource.REST_QUOTE})

PURPOSE_POLICIES = {
    # Exposure increases: fresh quote/trade within 60s, broker-position
    # fallback within 120s, fills never (spec 5.1).
    MarkPurpose.DECISION: PurposePolicy(
        primary_sources=_INCREASE_SOURCES,
        primary_max_age_seconds=60.0,
        fallback_sources=frozenset({MarkSource.BROKER_POSITION}),
        fallback_max_age_seconds=120.0,
        forbidden_sources=frozenset({MarkSource.FILL}),
        max_clock_skew_seconds=10.0,
        allow_halted=False,
        reject_invalid_quotes=True,
    ),
    # Immediately-before-submit recheck: registered tighter windows.
    MarkPurpose.SUBMISSION: PurposePolicy(
        primary_sources=_INCREASE_SOURCES,
        primary_max_age_seconds=30.0,
        fallback_sources=frozenset({MarkSource.BROKER_POSITION}),
        fallback_max_age_seconds=60.0,
        forbidden_sources=frozenset({MarkSource.FILL}),
        max_clock_skew_seconds=10.0,
        allow_halted=False,
        reject_invalid_quotes=True,
    ),
    # Risk reduction must remain available from independently refreshed broker
    # state even when degraded — but the degraded source/age is recorded.
    MarkPurpose.RISK_REDUCTION: PurposePolicy(
        primary_sources=_INCREASE_SOURCES,
        primary_max_age_seconds=60.0,
        fallback_sources=frozenset({
            MarkSource.BROKER_POSITION, MarkSource.REST_QUOTE,
            MarkSource.STREAM_QUOTE, MarkSource.STREAM_TRADE, MarkSource.FILL}),
        fallback_max_age_seconds=600.0,
        forbidden_sources=frozenset(),
        max_clock_skew_seconds=120.0,
        allow_halted=True,
        reject_invalid_quotes=False,
    ),
}


@dataclass(frozen=True)
class PurposeCheck:
    allowed: bool
    degraded: bool = False
    reasons: tuple = ()


def evaluate_mark(mark, purpose, now):
    """Evaluate one mark against a purpose policy.

    Returns ``PurposeCheck``: exposure-increase purposes fail closed on any
    defect; RISK_REDUCTION stays available but records every degradation.
    """
    policy = PURPOSE_POLICIES[purpose]
    reasons = []
    degraded = False
    age = mark.age_seconds(now)

    if mark.source in policy.forbidden_sources:
        return PurposeCheck(False, reasons=(
            f"forbidden source {mark.source.value} (fill/execution marks cannot "
            f"authorize {purpose.value})",))

    if mark.source in policy.primary_sources:
        if age > policy.primary_max_age_seconds:
            return PurposeCheck(False, reasons=(
                f"stale primary mark: age {age:.1f}s > "
                f"{policy.primary_max_age_seconds:.0f}s",))
    elif mark.source in policy.fallback_sources:
        if age > policy.fallback_max_age_seconds:
            return PurposeCheck(False, reasons=(
                f"stale fallback mark: age {age:.1f}s > "
                f"{policy.fallback_max_age_seconds:.0f}s",))
        degraded = True
        reasons.append(f"fallback source {mark.source.value} age {age:.1f}s")
    else:
        return PurposeCheck(False, reasons=(
            f"source {mark.source.value} not allowed for {purpose.value}",))

    if mark.clock_skew_seconds() > policy.max_clock_skew_seconds:
        return PurposeCheck(False, reasons=(
            f"clock skew {mark.clock_skew_seconds():.1f}s exceeds "
            f"{policy.max_clock_skew_seconds:.0f}s",))

    if mark.halt_signaled():
        if not policy.allow_halted:
            return PurposeCheck(False, reasons=("halt/LULD condition present",))
        degraded = True
        reasons.append("halt/LULD condition present")

    if mark.is_crossed_or_locked() or mark.has_empty_side():
        if policy.reject_invalid_quotes:
            return PurposeCheck(False, reasons=("crossed/locked or empty-size quote",))
        degraded = True
        reasons.append("crossed/locked or empty-size quote")

    if mark.quality is MarkQuality.EXECUTION_ONLY and purpose is not MarkPurpose.RISK_REDUCTION:
        return PurposeCheck(False, reasons=("execution-only quality",))

    if purpose is MarkPurpose.RISK_REDUCTION:
        if age > policy.degraded_age_seconds and not degraded:
            degraded = True
            reasons.append(f"aged mark {age:.1f}s from {mark.source.value}")
        if mark.quality in (MarkQuality.BROKER_DERIVED, MarkQuality.EXECUTION_ONLY) and not degraded:
            degraded = True
            reasons.append(f"degraded quality {mark.quality.value}")

    return PurposeCheck(True, degraded=degraded, reasons=tuple(reasons))


_EASTERN = ZoneInfo("America/New_York")


def classify_session(observed_at):
    """Coarse US-equity session bucket for a tz-aware timestamp.

    Weekday clock buckets only — exchange holidays/early closes are resolved by
    the strict trading calendar layer (Task 7A), not here.
    """
    _require_aware("observed_at", observed_at)
    local = observed_at.astimezone(_EASTERN)
    if local.weekday() >= 5:
        return "closed"
    minutes = local.hour * 60 + local.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "after"
    return "closed"
