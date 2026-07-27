"""Event-time availability contracts for causal backtests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


UTC = timezone.utc


def test_hour_bar_close_is_not_available_until_interval_end():
    from event_time import bar_available_at

    bar = {"t": "2026-03-02T14:00:00Z", "c": 123.0}

    assert bar_available_at(
        bar,
        interval=timedelta(hours=1),
        session_close_resolver=None,
    ) == datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def test_daily_equity_bar_uses_exchange_session_close():
    from event_time import bar_available_at

    calls = []

    def _session_close(start):
        calls.append(start)
        return datetime(2026, 3, 2, 21, 0, tzinfo=UTC)

    available = bar_available_at(
        {"t": "2026-03-02T00:00:00Z", "c": 123.0},
        interval=timedelta(days=1),
        session_close_resolver=_session_close,
    )

    assert available == datetime(2026, 3, 2, 21, 0, tzinfo=UTC)
    assert calls == [datetime(2026, 3, 2, 0, 0, tzinfo=UTC)]


def test_event_time_boundary_rejects_naive_datetimes():
    from event_time import BarInterval, SimulationClock, bar_available_at

    naive = datetime(2026, 3, 2, 14, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        BarInterval(naive, naive + timedelta(hours=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        SimulationClock(
            decision_at=naive,
            available_through=naive,
            execute_not_before=naive + timedelta(hours=1),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        bar_available_at(
            {"t": naive, "c": 123.0},
            interval=timedelta(hours=1),
            session_close_resolver=None,
        )


def test_simulation_clock_requires_execution_after_decision_event():
    from event_time import SimulationClock

    decision_at = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="after decision_at"):
        SimulationClock(
            decision_at=decision_at,
            available_through=decision_at,
            execute_not_before=decision_at,
        )


def test_actual_fetched_timeframe_controls_bar_interval():
    from event_time import interval_for_timeframe

    assert interval_for_timeframe("15Min") == timedelta(minutes=15)
    assert interval_for_timeframe("1Hour") == timedelta(hours=1)
    assert interval_for_timeframe("1Day") == timedelta(days=1)


def test_unknown_fetched_timeframe_fails_closed():
    from event_time import interval_for_timeframe

    with pytest.raises(ValueError, match="unsupported fetched timeframe"):
        interval_for_timeframe("20Min")
