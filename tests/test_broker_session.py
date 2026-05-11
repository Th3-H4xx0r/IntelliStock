"""Tests for backend/broker_session.py session-time helpers.

These verify the NYSE-aware live gate that replaces the old PT 1AM-5PM
window. The legacy gate had no holiday awareness, so it accepted Memorial
Day, Christmas, etc. as in-session. The new gate delegates to
``live_calendar.is_nyse_open_extended`` which uses ``exchange_calendars``
for full holiday and early-close handling.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from datetime import timezone

import pytest


# Make backend importable (mirrors tests/test_robinhood_adapter_smoke.py).
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


from broker_session import (  # noqa: E402
    is_within_legacy_pt_window,
    is_within_live_session,
    next_market_open_utc,
)


def test_legacy_gate_too_wide_on_nyse_holiday():
    """Memorial Day 2026 (Mon May 25) at 14 UTC is 10 AM ET / 7 AM PT.

    Legacy PT gate (1 AM-5 PM PT, weekday-only) accepts it - this is the
    bug. The new NYSE-aware gate rejects it because Memorial Day is not
    a NYSE session.

    Skipped when exchange_calendars is not installed (the fallback
    weekday-only check cannot detect holidays).
    """
    pytest.importorskip("exchange_calendars")
    t = dt.datetime(2026, 5, 25, 14, 0, 0, tzinfo=timezone.utc)
    assert is_within_live_session(t) is False
    # Legacy gate accepted it - this is the bug the new gate fixes.
    assert is_within_legacy_pt_window(t) is True


def test_market_open_inside_live_session():
    """10 AM ET (14 UTC) is RTH."""
    t = dt.datetime(2026, 4, 30, 14, 0, 0, tzinfo=timezone.utc)
    assert is_within_live_session(t) is True


def test_weekend_outside_live_session():
    t = dt.datetime(2026, 5, 2, 14, 0, 0, tzinfo=timezone.utc)  # Saturday
    assert is_within_live_session(t) is False


def test_next_market_open_returns_none_during_session():
    """Mid-session call must return None so callers don't print bogus
    `market opens in 60m` warnings.
    """
    t = dt.datetime(2026, 4, 30, 14, 0, 0, tzinfo=timezone.utc)
    result = next_market_open_utc(t)
    assert result is None


def test_next_market_open_returns_future_when_closed():
    """Saturday call should return Monday's open (or later)."""
    t = dt.datetime(2026, 5, 2, 14, 0, 0, tzinfo=timezone.utc)
    result = next_market_open_utc(t)
    if result is not None:  # exchange_calendars may not be installed
        assert result > t


def test_rth_only_flag_rejects_pre_market(monkeypatch):
    """2026-05-01: RH_RTH_ONLY=true narrows the live gate to RTH so
    pre-market submits don't run. 5 AM ET (09:00 UTC) is pre-market —
    extended-hours gate accepts, RTH-only gate rejects.
    """
    pre_market = dt.datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)  # Fri 5 AM ET
    # Default — pre-market accepted (extended hours).
    monkeypatch.delenv("RH_RTH_ONLY", raising=False)
    monkeypatch.delenv("LIVE_RTH_ONLY", raising=False)
    assert is_within_live_session(pre_market) is True
    # With flag — pre-market rejected.
    monkeypatch.setenv("RH_RTH_ONLY", "true")
    assert is_within_live_session(pre_market) is False


def test_rth_only_flag_rejects_after_hours(monkeypatch):
    """6 PM ET (22:00 UTC) is after-hours. RTH-only rejects."""
    after_hours = dt.datetime(2026, 5, 1, 22, 0, 0, tzinfo=timezone.utc)  # Fri 6 PM ET
    monkeypatch.delenv("RH_RTH_ONLY", raising=False)
    monkeypatch.delenv("LIVE_RTH_ONLY", raising=False)
    assert is_within_live_session(after_hours) is True
    monkeypatch.setenv("RH_RTH_ONLY", "true")
    assert is_within_live_session(after_hours) is False


def test_rth_only_flag_accepts_rth(monkeypatch):
    """10 AM ET (14:00 UTC) on a NYSE session is RTH; RTH-only accepts."""
    rth = dt.datetime(2026, 5, 1, 14, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("RH_RTH_ONLY", "true")
    assert is_within_live_session(rth) is True


def test_live_rth_only_alias_works(monkeypatch):
    """LIVE_RTH_ONLY is a synonym for RH_RTH_ONLY (older deployments may
    use the more-generic env name).
    """
    pre_market = dt.datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)
    monkeypatch.delenv("RH_RTH_ONLY", raising=False)
    monkeypatch.setenv("LIVE_RTH_ONLY", "true")
    assert is_within_live_session(pre_market) is False
