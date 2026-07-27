"""Causal event-time contracts for historical simulation.

Market bars are labeled by interval start, but their completed OHLCV values are
not observable until the interval ends.  This module centralizes that
distinction and keeps all boundary datetimes timezone-aware in UTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


UTC = timezone.utc


def interval_for_timeframe(timeframe: str) -> timedelta:
    """Return the exact interval represented by a fetched Alpaca timeframe."""
    normalized = str(timeframe or "").strip().lower()
    intervals = {
        "1min": timedelta(minutes=1),
        "5min": timedelta(minutes=5),
        "15min": timedelta(minutes=15),
        "30min": timedelta(minutes=30),
        "1hour": timedelta(hours=1),
        "1day": timedelta(days=1),
    }
    try:
        return intervals[normalized]
    except KeyError as exc:
        raise ValueError(
            f"unsupported fetched timeframe: {timeframe!r}"
        ) from exc


def aware_utc(value: datetime | str, *, field: str) -> datetime:
    """Return ``value`` normalized to aware UTC, rejecting naive timestamps."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError(f"{field} must be a non-empty timestamp")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError(f"{field} must be a datetime or ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class BarInterval:
    """One immutable market-data interval in aware UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = aware_utc(self.start, field="BarInterval.start")
        end = aware_utc(self.end, field="BarInterval.end")
        if end <= start:
            raise ValueError("BarInterval.end must be after start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True)
class SimulationClock:
    """Visibility and execution boundaries for one decision event."""

    decision_at: datetime
    available_through: datetime
    execute_not_before: datetime

    def __post_init__(self) -> None:
        decision_at = aware_utc(self.decision_at, field="decision_at")
        available_through = aware_utc(
            self.available_through,
            field="available_through",
        )
        execute_not_before = aware_utc(
            self.execute_not_before,
            field="execute_not_before",
        )
        if available_through > decision_at:
            raise ValueError("available_through cannot exceed decision_at")
        if execute_not_before <= decision_at:
            raise ValueError("execute_not_before must be after decision_at")
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "available_through", available_through)
        object.__setattr__(self, "execute_not_before", execute_not_before)


def bar_available_at(
    bar: dict,
    *,
    interval: timedelta | BarInterval,
    session_close_resolver: Callable[[datetime], datetime | None] | None,
) -> datetime:
    """Return when a completed bar becomes observable.

    Intraday/continuous bars become visible at ``start + interval``.  Daily
    equity bars use the injected exchange-session close so holidays and early
    closes remain the caller's calendar responsibility.  Crypto callers pass
    no session resolver and retain continuous 24-hour intervals.
    """
    if not isinstance(bar, dict):
        raise ValueError("bar must be a mapping")
    start = aware_utc(bar.get("t"), field="bar.t")

    if isinstance(interval, BarInterval):
        if interval.start != start:
            raise ValueError("BarInterval.start must match bar.t")
        return interval.end
    if not isinstance(interval, timedelta) or interval <= timedelta(0):
        raise ValueError("interval must be a positive timedelta or BarInterval")

    if interval >= timedelta(days=1) and session_close_resolver is not None:
        resolved = session_close_resolver(start)
        if resolved is None:
            raise ValueError("exchange session close is unavailable")
        available = aware_utc(resolved, field="session_close")
        if available <= start:
            raise ValueError("session close must be after bar start")
        return available
    return start + interval


def make_bar_availability_resolver(
    *,
    interval: timedelta,
    session_close_resolver: Callable[[datetime], datetime | None] | None,
) -> Callable[[dict], datetime]:
    """Bind an interval/calendar policy for cursor injection."""
    if not isinstance(interval, timedelta) or interval <= timedelta(0):
        raise ValueError("interval must be a positive timedelta")

    def _resolve(bar: dict) -> datetime:
        return bar_available_at(
            bar,
            interval=interval,
            session_close_resolver=session_close_resolver,
        )

    return _resolve
