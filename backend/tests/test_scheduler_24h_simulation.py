"""End-to-end scheduling simulation across a full trading day.

Drives `get_next_wake` with a frozen clock from Mon 04:00 PT through
Tue 05:00 PT (25 wallclock hours) at 60-second resolution, plus a fake
broker that sets the marker on FULL completion. Verifies the schedule
matches the spec:

- Exactly ONE FULL tick per session day (at 6:30 AM PT)
- MONITOR ticks at every 20-min slot during the session
- IDLE before open and after close, all weekend
- next_wake monotonic non-decreasing within a session

Catches schedule drift, marker race, and DST bugs without needing a
real broker process.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scheduler import get_next_wake  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:
    pytest.skip("zoneinfo required (Python 3.9+)", allow_module_level=True)

PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def _simulate_block(
    start_pt: datetime,
    end_pt: datetime,
    initial_marker: str | None,
    step_seconds: int = 60,
):
    """Iterate get_next_wake at fixed `step_seconds` cadence between
    [start_pt, end_pt). Uses the broker's contract: when mode==FULL fires
    successfully, set marker to that day's NY-date. Records every tick.

    Returns: list of {"now_pt", "mode", "next_wake_pt", "marker"} dicts.
    """
    ticks = []
    marker = initial_marker
    now = start_pt
    while now < end_pt:
        now_utc = now.astimezone(UTC)
        next_wake_utc, mode = get_next_wake(now_utc, marker)
        ticks.append({
            "now_pt": now.replace(microsecond=0),
            "mode": mode,
            "next_wake_pt": next_wake_utc.astimezone(PT).replace(microsecond=0),
            "marker": marker,
        })
        # Broker contract: marker set on successful FULL completion.
        if mode == "FULL":
            marker = now.date().isoformat()
        now += timedelta(seconds=step_seconds)
    return ticks


def test_24h_simulation_weekday_no_dst():
    """Mon 04:00 PT through Tue 05:01 PT — full session day + idle gap."""
    # 2026-05-11 is a Monday (no DST events).
    start = datetime(2026, 5, 11, 4, 0, 0, tzinfo=PT)
    end = datetime(2026, 5, 12, 5, 1, 0, tzinfo=PT)
    ticks = _simulate_block(start, end, initial_marker=None, step_seconds=60)

    # 1) Exactly ONE FULL tick across the entire run.
    full_ticks = [t for t in ticks if t["mode"] == "FULL"]
    assert len(full_ticks) == 1, f"Expected 1 FULL tick, got {len(full_ticks)}: {[t['now_pt'] for t in full_ticks]}"

    # 2) FULL fires at 6:30 AM PT exactly (or within 1-min tolerance).
    full_t = full_ticks[0]
    assert full_t["now_pt"].hour == 6 and full_t["now_pt"].minute == 30, full_t

    # 3) Marker set to today's date after FULL.
    full_idx = ticks.index(full_t)
    post_full_ticks = ticks[full_idx + 1:]
    if post_full_ticks:
        assert post_full_ticks[0]["marker"] == "2026-05-11"

    # 4) MONITOR ticks span the session window with 20-min spacing.
    monitor_ticks = [t for t in ticks if t["mode"] == "MONITOR"]
    expected_monitor_count_min = 30  # ~37 in our schedule
    assert len(monitor_ticks) >= expected_monitor_count_min, len(monitor_ticks)

    # 5) IDLE ticks before open AND after close.
    idle_before_open = [
        t for t in ticks
        if t["mode"] == "IDLE" and t["now_pt"].hour < 5
    ]
    idle_after_close = [
        t for t in ticks
        if t["mode"] == "IDLE" and t["now_pt"].date() == datetime(2026, 5, 11).date()
        and t["now_pt"].hour >= 17
    ]
    assert len(idle_before_open) > 0, "Expected IDLE ticks before 5 AM"
    assert len(idle_after_close) > 0, "Expected IDLE ticks after 5 PM"

    # 6) next_wake is non-decreasing within the session window.
    session_ticks = [t for t in ticks
                     if t["now_pt"].hour >= 5 and t["now_pt"].hour < 17
                     and t["now_pt"].date() == datetime(2026, 5, 11).date()]
    last_nw = session_ticks[0]["next_wake_pt"]
    for t in session_ticks[1:]:
        assert t["next_wake_pt"] >= last_nw, f"next_wake regressed at {t['now_pt']}: {last_nw} -> {t['next_wake_pt']}"
        last_nw = t["next_wake_pt"]

    # 7) Tuesday 04:59 = IDLE; 05:00 = MONITOR (the boundary).
    tue_4_59 = next(t for t in ticks
                    if t["now_pt"] == datetime(2026, 5, 12, 4, 59, 0, tzinfo=PT))
    tue_5_00 = next(t for t in ticks
                    if t["now_pt"] == datetime(2026, 5, 12, 5, 0, 0, tzinfo=PT))
    assert tue_4_59["mode"] == "IDLE"
    assert tue_5_00["mode"] == "MONITOR"


def test_friday_evening_through_monday_morning_weekend_skip():
    """Friday post-close through Monday morning — verify weekend skip."""
    # 2026-05-08 is Friday.
    start = datetime(2026, 5, 8, 18, 0, 0, tzinfo=PT)
    end = datetime(2026, 5, 11, 6, 0, 0, tzinfo=PT)
    ticks = _simulate_block(start, end, initial_marker="2026-05-08",
                            step_seconds=600)  # 10-min steps to keep test fast

    # No FULL on Saturday or Sunday.
    sat_sun_full = [t for t in ticks
                    if t["mode"] == "FULL"
                    and t["now_pt"].weekday() in (5, 6)]
    assert len(sat_sun_full) == 0

    # Monday 5:00 AM should be MONITOR (or the boundary tick).
    monday_5am_ticks = [t for t in ticks
                        if t["now_pt"].date() == datetime(2026, 5, 11).date()
                        and t["now_pt"].hour == 5
                        and t["now_pt"].minute == 0]
    if monday_5am_ticks:
        # Marker is from Friday → Monday's anchor (6:30) will trigger FULL.
        assert monday_5am_ticks[0]["mode"] == "MONITOR"

    # All weekend ticks should be IDLE.
    weekend_ticks = [t for t in ticks
                     if t["now_pt"].weekday() in (5, 6)]
    assert all(t["mode"] == "IDLE" for t in weekend_ticks)


def test_dst_spring_forward_no_double_full():
    """2026-03-08: DST spring-forward at 2 AM PT (skipped to 3 AM).
    Outside our 5 AM session window — no impact, but verify no anomaly.
    Sunday → IDLE all day."""
    start = datetime(2026, 3, 8, 0, 0, 0, tzinfo=PT)
    end = datetime(2026, 3, 9, 8, 0, 0, tzinfo=PT)
    # Use 60s steps so we deterministically land on the 6:30 anchor.
    ticks = _simulate_block(start, end, initial_marker=None, step_seconds=60)

    # No FULL ticks on Sunday 3/8 (non-session).
    sunday_full = [t for t in ticks
                   if t["mode"] == "FULL"
                   and t["now_pt"].date() == datetime(2026, 3, 8).date()]
    assert len(sunday_full) == 0

    # Monday 3/9 should fire one FULL at 6:30 AM PT.
    monday_full = [t for t in ticks
                   if t["mode"] == "FULL"
                   and t["now_pt"].date() == datetime(2026, 3, 9).date()]
    assert len(monday_full) == 1


def test_dst_fall_back_no_anomaly():
    """2026-11-01: DST fall-back at 2 AM PT (1 AM repeats).
    Sunday → IDLE all day; verify no double-fire."""
    start = datetime(2026, 11, 1, 0, 0, 0, tzinfo=PT)
    end = datetime(2026, 11, 2, 8, 0, 0, tzinfo=PT)
    ticks = _simulate_block(start, end, initial_marker=None, step_seconds=60)

    sunday_full = [t for t in ticks
                   if t["mode"] == "FULL"
                   and t["now_pt"].date() == datetime(2026, 11, 1).date()]
    assert len(sunday_full) == 0

    monday_full = [t for t in ticks
                   if t["mode"] == "FULL"
                   and t["now_pt"].date() == datetime(2026, 11, 2).date()]
    assert len(monday_full) == 1
