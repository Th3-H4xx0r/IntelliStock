"""Broker session-time helpers.

Extracted from broker.py so tests can import without triggering the
broker subprocess's module-level argparse, DB connections, and adapter
build. Pure functions only - no side effects.
"""

from __future__ import annotations

import datetime
from datetime import timezone
from typing import Optional


# Legacy PT window - used by backtest determinism path. Width is 1AM-5PM PT
# to cover Alpaca extended hours (4AM-8PM ET = 1AM-5PM PT).
_TRADING_DAY_START_HOUR_LIVE = 1
_TRADING_DAY_END_HOUR_LIVE = 17


def is_within_legacy_pt_window(
    current_time: datetime.datetime,
    start_hour: int = _TRADING_DAY_START_HOUR_LIVE,
    end_hour: int = _TRADING_DAY_END_HOUR_LIVE,
) -> bool:
    """Original broker.py PT-hour gate. Kept for backtest determinism.
    Live mode now uses ``is_within_live_session`` (NYSE-aware).

    ``start_hour``/``end_hour`` default to the LIVE window (1-17 PT).
    Backtest callers pass (5, 20) to preserve legacy reproducibility.
    """
    if current_time is None:
        return False
    try:
        from zoneinfo import ZoneInfo
        _pt = ZoneInfo("America/Los_Angeles")
        if current_time.tzinfo is None:
            ct = current_time.replace(tzinfo=timezone.utc)
        else:
            ct = current_time
        ct_pt = ct.astimezone(_pt)
    except Exception:
        ct_pt = current_time
    if ct_pt.weekday() >= 5:
        return False
    return start_hour <= ct_pt.hour < end_hour


def is_within_live_session(current_time: datetime.datetime) -> bool:
    """Live tick gate: NYSE pre-market + RTH + after-hours, holiday-aware.
    Wraps live_calendar.is_nyse_open_extended.

    2026-05-01: ``RH_RTH_ONLY=true`` (or ``LIVE_RTH_ONLY=true``) narrows the
    gate to regular hours only (9:30 AM - 4:00 PM ET). Eliminates pre-market
    quote disagreements between Alpaca IEX and RH (wide spreads on low-volume
    microcaps caused buy sizing to use the ask side at submit time and over-
    pay relative to the strategy's evaluation price). Default behavior is
    unchanged — extended hours still allowed unless the flag is set.
    """
    if current_time is None:
        return False
    import os
    _rth_only = (
        os.environ.get("RH_RTH_ONLY", "")
        or os.environ.get("LIVE_RTH_ONLY", "")
        or ""
    ).strip().lower() in ("1", "true", "yes")
    try:
        from live_calendar import is_nyse_open_extended, is_nyse_open
    except Exception:
        return is_within_legacy_pt_window(current_time)
    ct = current_time if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)
    try:
        if _rth_only:
            return bool(is_nyse_open(ct))
        return bool(is_nyse_open_extended(ct))
    except Exception:
        return is_within_legacy_pt_window(current_time)


def next_market_open_utc(current_time: datetime.datetime) -> Optional[datetime.datetime]:
    """Returns the next NYSE RTH open UTC, or None if already in session.

    Replaces broker.py:4567-4570 fallback that returned ``current_time + 1h``
    even mid-session, producing the bogus "60m to next open" log.
    """
    if current_time is None:
        return None
    if is_within_live_session(current_time):
        return None
    try:
        from live_calendar import next_open_utc as _calendar_next
        ct = current_time if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)
        return _calendar_next(ct)
    except Exception:
        return None


def advance_backtest_time(current_time, increment_td, is_crypto, is_within_session, next_market_open):
    """Advance the backtest clock by one step.

    Crypto trades 24/7/365, so it always advances by the raw cadence increment
    with NO session gate. Equity advances by the increment while inside the
    trading session, otherwise skips to the next market open (overnight/weekend).

    ``is_within_session`` and ``next_market_open`` are injected callables of
    ``current_time`` (broker.py passes its mode-aware wrappers) so this helper
    stays free of broker.py's import-time side effects. Returns the next
    ``current_time``; the caller logs the "skip" only when the returned time is
    not the raw increment (equity, outside session).
    """
    if is_crypto:
        return current_time + increment_td
    if is_within_session(current_time):
        return current_time + increment_td
    nxt = next_market_open(current_time)
    return nxt if nxt is not None else current_time + increment_td


def next_legacy_pt_open_utc(
    current_time: datetime.datetime,
    start_hour: int = _TRADING_DAY_START_HOUR_LIVE,
    end_hour: int = _TRADING_DAY_END_HOUR_LIVE,
) -> Optional[datetime.datetime]:
    """Legacy PT-window "next open" returning naive UTC. Kept for backtest
    determinism (broker.py:2281 builds daily-bar opens; broker.py:6929
    skips to next open during backtest replay). Live paths use
    ``next_market_open_utc`` instead.

    Returns None if ``current_time`` is None. Mid-window weekday calls
    return ``current_time + 1h`` to match the original broker.py
    behavior (callers in backtest depend on always getting *some*
    forward-progress timestamp).
    """
    if current_time is None:
        return None
    try:
        import pytz
        pt_tz = pytz.timezone("America/Los_Angeles")
        if getattr(current_time, "tzinfo", None) is None:
            ct = pytz.UTC.localize(current_time)
        else:
            ct = current_time
        pt_time = ct.astimezone(pt_tz)
        weekday = pt_time.weekday()
        hour = pt_time.hour
        target_date = pt_time.date()
        if hour >= end_hour or weekday >= 5:
            target_date = target_date + datetime.timedelta(days=1)
            while target_date.weekday() >= 5:
                target_date = target_date + datetime.timedelta(days=1)
            next_open_pt = pt_tz.localize(
                datetime.datetime.combine(target_date, datetime.time(start_hour, 0, 0))
            )
            next_open_utc_dt = next_open_pt.astimezone(pytz.UTC)
            return next_open_utc_dt.replace(tzinfo=None)
        if hour < start_hour:
            next_open_pt = pt_tz.localize(
                datetime.datetime.combine(target_date, datetime.time(start_hour, 0, 0))
            )
            next_open_utc_dt = next_open_pt.astimezone(pytz.UTC)
            return next_open_utc_dt.replace(tzinfo=None)
        # Inside session - preserve original behavior of returning a
        # forward-progress timestamp so backtest loops never stall.
        ct_naive = current_time if getattr(current_time, "tzinfo", None) is None else current_time.replace(tzinfo=None)
        return ct_naive + datetime.timedelta(seconds=3600)
    except Exception:
        ct_naive = current_time if getattr(current_time, "tzinfo", None) is None else current_time.replace(tzinfo=None)
        return ct_naive + datetime.timedelta(seconds=3600)
