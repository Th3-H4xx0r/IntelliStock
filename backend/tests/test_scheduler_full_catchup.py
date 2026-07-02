"""FULL catch-up: a missed 6:30 PT anchor must re-fire FULL later the same day.

Regression for 2026-07-01: 15 restarts spanned the open; the single FULL
anchor slot passed during churn and the strategy ran MONITOR-only all day,
never evaluating exits while CRWV fell to -19%.

Note on slot alignment: get_next_wake only "fires" a slot when `now` is within
the alignment tolerance of a real slot in the grid (open-aligned every 20 min,
plus the 6:30 anchor). Off-grid instants (e.g. 5:35, 6:29) return IDLE. The
before-anchor and anchor-slot cases below therefore use slot-aligned wallclocks
(5:40 = 340 min, 6:30 = 390 min anchor) so they exercise MONITOR/FULL rather
than IDLE, matching the real return contract in scheduler.py.
"""
import sys, os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scheduler import get_next_wake, DEFAULT_CONFIG

PT = timezone(timedelta(hours=-7))  # PDT (matches July dates used below)


def _pt(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=PT).astimezone(timezone.utc)


def test_missed_anchor_catches_up_to_full():
    # 9:00 PT, anchor (6:30) passed, marker is stale (yesterday) -> FULL
    wake, mode = get_next_wake(_pt(2026, 7, 1, 9, 0), "2026-06-30", DEFAULT_CONFIG)
    assert mode == "FULL"


def test_completed_marker_stays_monitor():
    # marker == today -> anchor already done -> MONITOR as before
    wake, mode = get_next_wake(_pt(2026, 7, 1, 9, 0), "2026-07-01", DEFAULT_CONFIG)
    assert mode == "MONITOR"


def test_before_anchor_stays_monitor():
    # 5:40 PT slot precedes the 6:30 anchor -> MONITOR (no early FULL)
    wake, mode = get_next_wake(_pt(2026, 7, 1, 5, 40), "2026-06-30", DEFAULT_CONFIG)
    assert mode == "MONITOR"


def test_anchor_slot_still_full():
    wake, mode = get_next_wake(_pt(2026, 7, 1, 6, 30), "2026-06-30", DEFAULT_CONFIG)
    assert mode == "FULL"


def test_no_marker_after_anchor_is_full():
    wake, mode = get_next_wake(_pt(2026, 7, 1, 12, 0), None, DEFAULT_CONFIG)
    assert mode == "FULL"
