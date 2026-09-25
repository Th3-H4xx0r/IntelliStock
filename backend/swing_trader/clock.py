"""NY session clock, the NYSE calendar, and the per-tick time budget.

Platform glue with no ST source. ST ran from UTC crons that drifted an hour
every winter (spec §9 item 12); here every schedule is an ET wall-clock time
evaluated on the live broker's own ticks, which land every 20 minutes from
05:00 PT (backend/scheduler.py DEFAULT_CONFIG): 09:15 fires at the 09:20 tick,
10:30 at 10:40, and 15:45 — which has no tick before the 16:00 close — fires
at 15:40 through the `lead_min` of at_or_after.

The budget: on a MONITOR tick broker.py waits 120 s for run_run_once_strategies
(_WATCHDOG_MONITOR_SEC) and then DISCARDS its output while the strategy's cache
writes still land; other ticks wait 1800 s. Both lanes of one document run in
that one call, so they share one deadline, stop starting network work before
it, and resume on the next tick from their cached state.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

TICK_GRID_MIN = 20
MONITOR_BUDGET_S = 100.0
DEFAULT_BUDGET_S = 1500.0
#: One candidate = earnings + sector bars + two model calls at 25 s each.
CANDIDATE_RESERVE_S = 55.0
#: The bars fetch (~6 batched requests) plus the VIX read.
PREPARE_RESERVE_S = 45.0

_DEADLINES: dict = {}
_CALENDAR = None


def as_utc(value):
    """tz-aware UTC, or None. Naive input is treated as UTC (the backtest clock
    is naive, the live clock aware; strategy_x._as_utc makes the same call)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ny_now(current_time) -> datetime:
    t = as_utc(current_time) or datetime.now(timezone.utc)
    return t.astimezone(NY)


def ny_date(current_time) -> str:
    return ny_now(current_time).date().isoformat()


def parse_hhmm(value, default: str = "00:00") -> dtime:
    for raw in (value, default):
        try:
            hh, mm = str(raw).strip().split(":", 1)
            return dtime(int(hh), int(mm))
        except (TypeError, ValueError):
            continue
    return dtime(0, 0)


def at_or_after(current_time, hhmm, *, lead_min: int = 0) -> bool:
    now = ny_now(current_time)
    t = parse_hhmm(hhmm)
    target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return now >= target - timedelta(minutes=int(lead_min))


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _calendar():
    global _CALENDAR
    if _CALENDAR is None:
        import exchange_calendars as _ec
        _CALENDAR = _ec.get_calendar("XNYS")
    return _CALENDAR


def trading_days(start: date, end: date) -> set:
    """NYSE sessions in [start, end]. Weekdays when exchange_calendars is
    missing — the fallback live_calendar also uses."""
    try:
        sessions = _calendar().sessions_in_range(start.isoformat(), end.isoformat())
        return {ts.date() for ts in sessions}
    except Exception:
        out, d = set(), start
        while d <= end:
            if d.weekday() < 5:
                out.add(d)
            d += timedelta(days=1)
        return out


def is_trading_day(d: date) -> bool:
    return d in trading_days(d, d)


def is_rth(current_time) -> bool:
    from live_calendar import is_nyse_open
    t = as_utc(current_time)
    return bool(t is not None and is_nyse_open(t))


def tick_deadline(current_time, mode, *, clock=_time.monotonic) -> float:
    key = (as_utc(current_time) or datetime.now(timezone.utc)).isoformat()
    if key not in _DEADLINES:
        _DEADLINES.clear()
        budget = (MONITOR_BUDGET_S if str(mode or "").upper() == "MONITOR"
                  else DEFAULT_BUDGET_S)
        _DEADLINES[key] = clock() + budget
    return _DEADLINES[key]


def time_left(deadline, *, clock=_time.monotonic) -> float:
    return float(deadline) - clock()
