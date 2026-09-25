"""Real-NYSE-calendar helpers shared by the swing-port backtest tests.

A plain module the tests import by name, like ``live_order_task8_helpers``.
It holds the resolvers the broker binds for daily equity bars -- when a bar
became known (its session close) and when it opened (its session open) --
and a builder for a daily bar labelled the way Alpaca labels one.
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import backtest_bar_events as bbe  # noqa: E402
from event_time import make_bar_availability_resolver  # noqa: E402


def session_close(bar_start):
    """The NYSE close for a daily bar's date (aware UTC), or None."""
    import live_calendar
    import pandas as pd
    session = pd.Timestamp(bar_start.date())
    if not live_calendar._CAL.is_session(session):
        return None
    return live_calendar._CAL.session_close(session).tz_convert(
        "UTC").to_pydatetime()


AVAILABLE = make_bar_availability_resolver(
    interval=timedelta(days=1), session_close_resolver=session_close)
OPENS = bbe.make_bar_open_resolver(
    interval=timedelta(days=1),
    session_open_resolver=bbe.equity_daily_session_open)


def daily_bar(date, o=100.0, h=101.0, l=99.0, c=100.5):
    """A daily bar for ``date`` (YYYY-MM-DD), labelled 05:00 UTC (midnight
    EST), as Alpaca labels one outside daylight saving time."""
    return {"t": f"{date}T05:00:00Z", "o": o, "h": h, "l": l, "c": c, "v": 1}
