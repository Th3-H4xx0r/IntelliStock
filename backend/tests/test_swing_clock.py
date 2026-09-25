"""NY clock, the NYSE calendar, and the budget both lanes share in one tick."""
import os
import sys
from datetime import date, datetime, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import clock  # noqa: E402

MON_0920_ET = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)   # EDT
MON_0940_ET = datetime(2026, 6, 1, 13, 40, tzinfo=timezone.utc)


def test_ny_date_uses_the_new_york_calendar():
    late = datetime(2026, 6, 2, 2, 30, tzinfo=timezone.utc)   # 22:30 ET Jun 1
    assert clock.ny_date(late) == "2026-06-01"
    assert clock.ny_date(datetime(2026, 6, 1, 13, 20)) == "2026-06-01"   # naive = UTC


def test_at_or_after_with_and_without_a_lead():
    assert clock.at_or_after(MON_0920_ET, "09:15") is True
    assert clock.at_or_after(MON_0920_ET, "09:30") is False
    # 15:40 ET is the last RTH tick; a 15:45 monitor with a 20-minute lead fires there.
    t = datetime(2026, 6, 1, 19, 40, tzinfo=timezone.utc)
    assert clock.at_or_after(t, "15:45") is False
    assert clock.at_or_after(t, "15:45", lead_min=clock.TICK_GRID_MIN) is True


def test_parse_hhmm_falls_back_on_garbage():
    assert clock.parse_hhmm("10:30").hour == 10
    assert clock.parse_hhmm("nonsense", "09:15").minute == 15


def test_trading_days_skip_weekends_and_good_friday():
    days = clock.trading_days(date(2026, 3, 30), date(2026, 4, 6))
    assert date(2026, 4, 2) in days          # Thursday
    assert date(2026, 4, 3) not in days      # Good Friday
    assert date(2026, 4, 4) not in days      # Saturday
    assert clock.is_trading_day(date(2026, 6, 1)) is True
    assert clock.is_trading_day(date(2026, 7, 3)) is False   # Independence Day observed


def test_is_rth():
    assert clock.is_rth(MON_0920_ET) is False
    assert clock.is_rth(MON_0940_ET) is True


def test_week_monday():
    assert clock.week_monday(date(2026, 6, 4)) == date(2026, 6, 1)


def test_both_lanes_share_one_deadline_per_tick():
    clock._DEADLINES.clear()
    now = [1000.0]
    fake = lambda: now[0]  # noqa: E731
    a = clock.tick_deadline(MON_0920_ET, "MONITOR", clock=fake)
    now[0] = 1030.0
    b = clock.tick_deadline(MON_0920_ET, "MONITOR", clock=fake)
    assert a == b == 1000.0 + clock.MONITOR_BUDGET_S
    assert clock.time_left(a, clock=fake) == clock.MONITOR_BUDGET_S - 30.0
    c = clock.tick_deadline(MON_0940_ET, "FULL", clock=fake)
    assert c == 1030.0 + clock.DEFAULT_BUDGET_S


def test_the_monitor_budget_fits_inside_the_broker_watchdog():
    # A candidate starts only with CANDIDATE_RESERVE_S left and takes at most
    # about that long, so a MONITOR tick ends by MONITOR_BUDGET_S (< 120 s).
    assert clock.MONITOR_BUDGET_S < 120.0
    assert clock.CANDIDATE_RESERVE_S < clock.MONITOR_BUDGET_S
    assert clock.PREPARE_RESERVE_S < clock.MONITOR_BUDGET_S
