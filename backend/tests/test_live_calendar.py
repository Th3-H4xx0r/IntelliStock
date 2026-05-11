"""NYSE calendar: weekend, RTH, boundaries."""
import os
import sys
from datetime import datetime, timezone

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import pytest

from live_calendar import is_nyse_open, next_open_utc


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        is_nyse_open(datetime(2026, 4, 21, 14, 30))  # naive


def test_weekend_closed():
    # Saturday 2026-04-18
    sat = datetime(2026, 4, 18, 14, 30, 0, tzinfo=timezone.utc)
    assert is_nyse_open(sat) is False


def test_weekday_rth_open():
    # Tuesday 2026-04-21 14:30 UTC = 10:30 ET = open
    tue = datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc)
    assert is_nyse_open(tue) is True


def test_weekday_after_close():
    # Tuesday 2026-04-21 21:00 UTC = 17:00 ET = closed
    tue_late = datetime(2026, 4, 21, 21, 0, 0, tzinfo=timezone.utc)
    assert is_nyse_open(tue_late) is False


def test_next_open_monotonic():
    dt = datetime(2026, 4, 18, 23, 0, 0, tzinfo=timezone.utc)  # Sat night
    nxt = next_open_utc(dt)
    assert nxt is not None and nxt > dt
