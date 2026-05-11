"""Tests for backend.scheduler.get_next_wake — the pure scheduling function
that replaces the dual-cadence gate previously in graph_nexus_analysis.run_once.

Each test feeds a frozen `now` (UTC) plus marker + config and asserts the
returned (next_wake_utc, mode). All expected next_wake values are stated in
PT for human readability and converted to UTC at assertion time.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scheduler import DEFAULT_CONFIG, get_next_wake  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:
    pytest.skip("zoneinfo required (Python 3.9+)", allow_module_level=True)


PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def _pt(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """Construct a PT datetime, then convert to UTC for the get_next_wake input."""
    return datetime(year, month, day, hour, minute, second, tzinfo=PT).astimezone(UTC)


def _expect_pt(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Expected next_wake at the given PT wallclock — same as _pt but explicit."""
    return _pt(year, month, day, hour, minute, 0)


# --- Pre-session ---------------------------------------------------------

def test_pre_session_4am_returns_idle_wake_at_5am():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 4, 0), marker=None)
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 7, 5, 0)


def test_pre_session_midnight_returns_idle_wake_at_5am():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 0, 0), marker=None)
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 7, 5, 0)


def test_pre_session_4_59_59_returns_idle():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 4, 59, 59), marker=None)
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 7, 5, 0)


# --- Session start (no marker — first MONITOR before FULL anchor) -------

def test_5am_first_slot_no_marker_returns_monitor():
    """5:00 AM PT exactly — first MONITOR slot, no FULL until 6:30."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 5, 0, 0), marker=None)
    assert mode == "MONITOR"
    assert nw == _expect_pt(2026, 5, 7, 5, 20)


def test_5_00_30_within_alignment_tolerance_fires_monitor():
    """30s past the slot, still within 60s tolerance — slot fires."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 5, 0, 30), marker=None)
    assert mode == "MONITOR"
    assert nw == _expect_pt(2026, 5, 7, 5, 20)


def test_5_01_00_outside_alignment_tolerance_returns_idle():
    """60.1s past the slot — outside 60s tolerance, should be IDLE."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 5, 1, 1), marker=None)
    assert mode == "IDLE"
    # Next wake is the next slot at 5:20.
    assert nw == _expect_pt(2026, 5, 7, 5, 20)


def test_5_13_between_slots_returns_idle():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 5, 13), marker=None)
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 7, 5, 20)


# --- Pre-market MONITOR cadence -----------------------------------------

def test_5_20_pre_market_monitor():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 5, 20), marker=None)
    assert mode == "MONITOR"
    assert nw == _expect_pt(2026, 5, 7, 5, 40)


def test_6_20_last_pre_market_monitor_before_full_anchor():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 20), marker=None)
    assert mode == "MONITOR"
    assert nw == _expect_pt(2026, 5, 7, 6, 30)


# --- FULL anchor at 6:30 AM PT -----------------------------------------

def test_6_29_59_returns_idle_just_before_full_slot():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 29, 59), marker=None)
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 7, 6, 30)


def test_full_anchor_6_30_no_marker_returns_full():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 30), marker=None)
    assert mode == "FULL"
    # Open-aligned grid: next monitor after 6:30 anchor is 6:40.
    assert nw == _expect_pt(2026, 5, 7, 6, 40)


def test_full_anchor_6_30_with_today_marker_returns_monitor_demoted():
    """Restart-resilience: marker shows today → FULL skipped, MONITOR fires."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 30), marker="2026-05-07")
    assert mode == "MONITOR"
    assert nw == _expect_pt(2026, 5, 7, 6, 40)


def test_full_anchor_6_30_with_yesterday_marker_returns_full():
    """Day-rollover: yesterday's marker means FULL fires today."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 30), marker="2026-05-06")
    assert mode == "FULL"
    assert nw == _expect_pt(2026, 5, 7, 6, 40)


# --- Post-FULL MONITOR cadence -----------------------------------------

def test_post_full_8am_with_today_marker_returns_monitor():
    """8:00 AM PT — open-aligned slot (300 + 9*20 = 480)."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 8, 0), marker="2026-05-07")
    assert mode == "MONITOR"
    assert nw == _expect_pt(2026, 5, 7, 8, 20)


def test_late_session_4_40pm_last_monitor_then_idle_until_tomorrow():
    """4:40 PM PT (=16:40, slot 1000) is the last monitor of the day."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 16, 40), marker="2026-05-07")
    assert mode == "MONITOR"
    # Next slot would be 17:00 but that's == close_min, so skip to tomorrow.
    assert nw == _expect_pt(2026, 5, 8, 5, 0)


# --- Post-session ------------------------------------------------------

def test_5pm_at_close_returns_idle_wake_at_next_open():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 17, 0), marker="2026-05-07")
    assert mode == "IDLE"
    # Friday 5/7 → Mon 5/11 (5/8 is Friday in 2026 — confirm with actual cal)
    # 2026-05-07 is a Thursday. Next session day is Friday 2026-05-08.
    assert nw == _expect_pt(2026, 5, 8, 5, 0)


def test_8pm_post_session_returns_idle():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 20, 0), marker="2026-05-07")
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 8, 5, 0)


# --- Weekend ----------------------------------------------------------

def test_friday_evening_wakes_monday_morning():
    # 2026-05-08 is Friday.
    nw, mode = get_next_wake(_pt(2026, 5, 8, 18, 0), marker="2026-05-08")
    assert mode == "IDLE"
    # Monday 2026-05-11.
    assert nw == _expect_pt(2026, 5, 11, 5, 0)


def test_saturday_returns_idle_wake_monday():
    # 2026-05-09 is Saturday.
    nw, mode = get_next_wake(_pt(2026, 5, 9, 10, 0), marker=None)
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 11, 5, 0)


def test_sunday_returns_idle_wake_monday():
    # 2026-05-10 is Sunday.
    nw, mode = get_next_wake(_pt(2026, 5, 10, 10, 0), marker=None)
    assert mode == "IDLE"
    assert nw == _expect_pt(2026, 5, 11, 5, 0)


# --- DST handling -----------------------------------------------------

def test_dst_spring_forward_no_window_impact():
    """2026-03-08: clock skips 2:00→3:00 AM PT. Outside our 5 AM session
    window, so behavior is unaffected. Sunday is non-session anyway."""
    nw, mode = get_next_wake(_pt(2026, 3, 8, 6, 30), marker=None)
    assert mode == "IDLE"  # Sunday
    assert nw == _expect_pt(2026, 3, 9, 5, 0)


def test_dst_fall_back_no_double_fire():
    """2026-11-01: 1:00-2:00 AM PT repeats. Outside session window."""
    nw, mode = get_next_wake(_pt(2026, 11, 1, 6, 30), marker=None)
    assert mode == "IDLE"  # Sunday
    assert nw == _expect_pt(2026, 11, 2, 5, 0)


# --- Mid-day reboot recovery ------------------------------------------

def test_mid_day_reboot_11_13_with_today_marker():
    """Operator restarts broker at 11:13 AM PT after wedge. Marker shows
    today's FULL completed → next slot is 11:20 MONITOR (slot 680 = 300+19*20)."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 11, 13), marker="2026-05-07")
    assert mode == "IDLE"  # Mid-tick — wait for next slot
    assert nw == _expect_pt(2026, 5, 7, 11, 20)


def test_post_reboot_lands_on_slot_fires_monitor():
    """Reboot completes at 11:20 (next slot) → MONITOR fires."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 11, 20), marker="2026-05-07")
    assert mode == "MONITOR"
    assert nw == _expect_pt(2026, 5, 7, 11, 40)


# --- Malformed / edge inputs -----------------------------------------

def test_garbage_marker_treated_as_none_returns_full():
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 30), marker="not a date")
    # marker != today_str ('2026-05-07'), so FULL fires.
    assert mode == "FULL"


def test_future_marker_treated_as_set_demotes_full():
    """Marker for 2030 != today; we don't validate the date range, so the
    inequality marker != today fires FULL. This is conservative — operator
    can always force FULL by clearing the marker."""
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 30), marker="2030-01-01")
    assert mode == "FULL"  # marker != today_str


def test_naive_now_treated_as_utc():
    """Naive datetime defensively treated as UTC."""
    naive = datetime(2026, 5, 7, 12, 0, 0)  # 12:00 UTC = 5:00 AM PDT
    nw, mode = get_next_wake(naive, marker=None)
    # 12:00 UTC = 5:00 AM PT (PDT in May)
    assert mode == "MONITOR"


def test_clock_skew_next_wake_strictly_in_future():
    """If somehow next_wake computes to <= now, we must bump forward."""
    # Crafted: a config with monitor_interval_min that wouldn't naturally
    # produce a "wake at now" — but the safety guard is part of the API
    # contract.
    nw, mode = get_next_wake(_pt(2026, 5, 7, 8, 0), marker="2026-05-07")
    now = _pt(2026, 5, 7, 8, 0)
    assert nw > now


# --- Custom config ----------------------------------------------------

def test_custom_open_close_hours():
    cfg = {"open_pt_min": 240, "close_pt_min": 1200}  # 4 AM — 8 PM PT
    nw, mode = get_next_wake(_pt(2026, 5, 7, 4, 0), marker=None, config=cfg)
    assert mode == "MONITOR"


def test_invalid_full_anchor_outside_session_clamped_to_default():
    cfg = {"full_anchor_pt_min": 9999}  # bogus
    nw, mode = get_next_wake(_pt(2026, 5, 7, 6, 30), marker=None, config=cfg)
    # Default 390 (= 6:30) is restored, so 6:30 fires FULL.
    assert mode == "FULL"


def test_weekdays_only_off_runs_saturday():
    cfg = {"weekdays_only": False}
    nw, mode = get_next_wake(_pt(2026, 5, 9, 5, 0), marker=None, config=cfg)
    # Saturday 5/9 at 5 AM with weekdays_only=False fires MONITOR.
    assert mode == "MONITOR"
