"""NYSE market calendar via exchange_calendars.

Falls back to a weekday+RTH approximation if exchange_calendars isn't installed,
but the fallback does NOT honor holidays or early closes - pip install it before
going live.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


try:
    import exchange_calendars as _ec  # type: ignore
    _CAL = _ec.get_calendar("XNYS")
    _HAS_LIB = True
except Exception:
    _CAL = None
    _HAS_LIB = False

try:
    import pytz as _pytz
    _ET = _pytz.timezone("America/New_York")
except Exception:
    _pytz = None
    _ET = None


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (UTC recommended)")
    return dt.astimezone(timezone.utc)


def is_nyse_open(now_utc: datetime) -> bool:
    """True only during regular trading hours (9:30 AM – 4:00 PM ET).

    For extended-hours trading (pre-market 4:00-9:30 AM ET + after-hours
    4:00-8:00 PM ET) use ``is_nyse_open_extended``.
    """
    now_utc = _ensure_utc(now_utc)
    if _HAS_LIB and _CAL is not None:
        try:
            # exchange_calendars works with pandas Timestamps
            import pandas as _pd
            ts = _pd.Timestamp(now_utc).tz_convert("UTC")
            return bool(_CAL.is_open_on_minute(ts.floor("min")))
        except Exception:
            pass
    # Fallback - weekday RTH only (no holiday handling)
    if _ET is None:
        return False
    et = now_utc.astimezone(_ET)
    if et.weekday() >= 5:
        return False
    rth_open = et.replace(hour=9, minute=30, second=0, microsecond=0)
    rth_close = et.replace(hour=16, minute=0, second=0, microsecond=0)
    return rth_open <= et < rth_close


def is_nyse_open_extended(now_utc: datetime) -> bool:
    """True during regular OR extended trading hours.

    Window: 4:00 AM – 8:00 PM ET on NYSE trading days (1:00 AM – 5:00 PM PT).
    Covers pre-market (4:00-9:30 AM ET), regular (9:30 AM – 4:00 PM ET), and
    after-hours (4:00-8:00 PM ET). Alpaca accepts extended-hours orders only
    for LIMIT + DAY TIF; the adapter enforces that separately.

    Uses the same NYSE trading-day calendar as ``is_nyse_open`` so holidays
    and weekends are correctly excluded. On an early-close day (1:00 PM ET),
    after-hours trading still runs until 5:00 PM ET per Alpaca; we keep the
    8:00 PM window for regularity.
    """
    now_utc = _ensure_utc(now_utc)
    # Fast reject: not a NYSE trading day at all (weekend / full holiday).
    if _HAS_LIB and _CAL is not None:
        try:
            import pandas as _pd
            ts = _pd.Timestamp(now_utc).tz_convert("UTC")
            # `sessions_in_range` includes the date if it's a trading day.
            date_ts = _pd.Timestamp(ts.date())
            if _CAL.is_session(date_ts) is False:
                return False
        except Exception:
            pass
    if _ET is None:
        return False
    et = now_utc.astimezone(_ET)
    if et.weekday() >= 5:
        return False
    ext_open = et.replace(hour=4, minute=0, second=0, microsecond=0)
    ext_close = et.replace(hour=20, minute=0, second=0, microsecond=0)
    return ext_open <= et < ext_close


def next_open_utc(after_utc: datetime) -> Optional[datetime]:
    after_utc = _ensure_utc(after_utc)
    if _HAS_LIB and _CAL is not None:
        try:
            import pandas as _pd
            horizon = after_utc + timedelta(days=14)
            sessions = _CAL.sessions_in_range(
                _pd.Timestamp(after_utc.date()),
                _pd.Timestamp(horizon.date()),
            )
            for s in sessions:
                m = _CAL.session_open(s)
                m_utc = m.tz_convert("UTC").to_pydatetime()
                if m_utc > after_utc:
                    return m_utc
        except Exception:
            pass
    # Fallback
    probe = after_utc + timedelta(hours=1)
    for _ in range(14 * 24):
        if probe.weekday() < 5:
            if _ET is not None:
                candidate_et = probe.astimezone(_ET).replace(hour=9, minute=30, second=0, microsecond=0)
                candidate_utc = candidate_et.astimezone(timezone.utc)
                if candidate_utc > after_utc:
                    return candidate_utc
        probe += timedelta(hours=1)
    return None


def next_close_utc(after_utc: datetime, *, extended: bool = False) -> Optional[datetime]:
    """Return the next NYSE close datetime (UTC) after ``after_utc``.

    ``extended=False``: regular hours close (16:00 ET, or 13:00 ET on early-close days).
    ``extended=True``: after-hours close (20:00 ET).

    Used by RobinhoodAdapter.submit_order to cap the inter-order delay
    so a 25-45 s sleep doesn't push a ``gfd`` market order past the close
    cutoff (HIGH agent finding #9). Returns None if the calendar layer
    can't resolve a session — caller should treat as "don't cap".
    """
    after_utc = _ensure_utc(after_utc)
    if _ET is None:
        return None
    if _HAS_LIB and _CAL is not None and not extended:
        try:
            import pandas as _pd
            ts = _pd.Timestamp(after_utc).tz_convert("UTC")
            session = _CAL.minute_to_session(ts.floor("min"))
            if session is not None:
                close = _CAL.session_close(session).tz_convert("UTC").to_pydatetime()
                if close > after_utc:
                    return close
        except Exception:
            pass
    et = after_utc.astimezone(_ET)
    if et.weekday() >= 5:
        return None
    target_hour = 20 if extended else 16
    candidate_et = et.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if candidate_et <= et:
        return None
    return candidate_et.astimezone(timezone.utc)


def nyse_session_close_utc(session_date) -> datetime:
    """Return the authoritative regular-session close for one XNYS session.

    Promotion evidence must honor holidays and early closes, so this helper
    deliberately has no weekday approximation. If the exchange calendar is
    unavailable or ``session_date`` is not an XNYS session, it fails closed.
    """
    if not _HAS_LIB or _CAL is None:
        raise RuntimeError(
            "exchange_calendars is required for authoritative XNYS closes"
        )
    try:
        import pandas as _pd

        session = _pd.Timestamp(session_date)
        if session.tzinfo is not None:
            session = session.tz_convert("UTC").tz_localize(None)
        session = session.normalize()
        if not bool(_CAL.is_session(session)):
            raise ValueError(f"{session.date()} is not an XNYS session")
        return (
            _CAL.session_close(session)
            .tz_convert("UTC")
            .to_pydatetime()
            .astimezone(timezone.utc)
        )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"could not resolve authoritative XNYS close for {session_date!r}"
        ) from exc


def is_early_close(now_utc: datetime) -> bool:
    """True if today is an early-close day (1pm ET) per NYSE calendar."""
    now_utc = _ensure_utc(now_utc)
    if _HAS_LIB and _CAL is not None:
        try:
            import pandas as _pd
            ts = _pd.Timestamp(now_utc).tz_convert("UTC")
            session = _CAL.minute_to_session(ts.floor("min"))
            close = _CAL.session_close(session).tz_convert("UTC").to_pydatetime()
            if _ET is not None:
                close_et = close.astimezone(_ET)
                return close_et.hour == 13
        except Exception:
            return False
    return False
