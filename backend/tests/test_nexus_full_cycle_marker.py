"""
Full-cycle marker behavior tests.

Marker: strategy_cache['_nexus_full_cycle_completed_date'] = NY-date string.
Written at the end of run_once when dual_cadence_active AND past_open_today.

Critical:
  - NOT written during _GN_LIVE_MODE_FLAG=False (backtest).
  - NOT written during historical_lookback_mode=True.
  - Restart-safe: when restored from RethinkDB on container restart,
    monitor mode resumes for the rest of the day.
  - NY-midnight rollover: previous day's marker triggers full cycle.
"""

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_live_mode_flag():
    gna._GN_LIVE_MODE_FLAG = False
    yield
    gna._GN_LIVE_MODE_FLAG = False


def _strategy():
    return gna.GraphNexusAnalysis()


def _live_config():
    return {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }


def test_marker_not_written_in_backtest():
    """_GN_LIVE_MODE_FLAG=False → gate inactive → marker never written."""
    strat = _strategy()
    config = {
        "_nexus_is_live_mode": False,  # backtest
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    cache = {}
    strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 15, 0),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    assert "_nexus_full_cycle_completed_date" not in cache


def test_marker_not_written_during_lookback():
    """historical_lookback_mode=True → gate inactive → marker never written."""
    strat = _strategy()
    config = _live_config()
    config["historical_lookback_mode"] = True
    cache = {}
    strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 15, 0),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    assert "_nexus_full_cycle_completed_date" not in cache


def test_marker_survives_simulated_restart():
    """Marker written by tick-N is restored to strategy_cache on tick-N+1
    (simulating container restart that loaded cache from RethinkDB)."""
    strat = _strategy()
    config = _live_config()
    # Tick 1: full cycle runs (no marker), simulated by hand-setting marker.
    cache_after_tick1 = {"_nexus_full_cycle_completed_date": "2026-05-01"}
    # Simulated restart: strategy_cache_persistence loads it back.
    cache_after_restart = dict(cache_after_tick1)
    # Tick 2 (post-restart, same day): monitor mode runs.
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 18, 0),  # 2 PM ET same day
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache_after_restart,
        time_increment=3600,
    )
    # Monitor cycle returns its dict (with metadata), and marker is unchanged.
    assert isinstance(out, dict)
    assert cache_after_restart["_nexus_full_cycle_completed_date"] == "2026-05-01"


def test_yesterday_marker_does_not_route_to_monitor():
    """Yesterday's marker + today's post-open tick → gate must NOT route to monitor.
    (Full pipeline would run; in a no-DB test env it may abort without writing the
    new marker, but the critical correctness signal is that monitor was NOT called.)"""
    strat = _strategy()
    config = _live_config()
    cache = {"_nexus_full_cycle_completed_date": "2026-04-30"}
    # Patch _run_monitor_cycle to detect if it was called.
    original_monitor = strat._run_monitor_cycle
    monitor_call_count = [0]

    def _spy_monitor(*args, **kwargs):
        monitor_call_count[0] += 1
        return original_monitor(*args, **kwargs)

    strat._run_monitor_cycle = _spy_monitor
    try:
        strat.run_once(
            symbols=[],
            prices={},
            current_time=_utc(2026, 5, 1, 15, 0),
            config=config,
            conditions={},
            portfolio_emulator=None,
            strategy_cache=cache,
            time_increment=3600,
        )
    except Exception:
        pass  # Pipeline may abort in test env; we only care about routing.
    assert monitor_call_count[0] == 0, "Monitor should NOT run on date mismatch"


def test_ny_date_str_handles_dst_correctly():
    """_ny_date_str must produce the right NY-local date around DST transitions.
    US DST 2026: spring-forward = March 8, fall-back = November 1."""
    # Spring-forward (March 8, 2026): last EST second is 06:59:59 UTC = 01:59 EST.
    # First EDT second is 07:00:00 UTC = 03:00 EDT (the 02:xx ET hour is skipped).
    last_est = _utc(2026, 3, 8, 6, 59)
    first_edt = _utc(2026, 3, 8, 7, 0)
    assert gna._ny_date_str(last_est) == "2026-03-08"
    assert gna._ny_date_str(first_edt) == "2026-03-08"

    # Fall-back (November 1, 2026): the 01:00-02:00 ET hour repeats.
    # 05:30 UTC = 01:30 EDT (first occurrence); 06:30 UTC = 01:30 EST (second).
    fall_back_first = _utc(2026, 11, 1, 5, 30)
    fall_back_second = _utc(2026, 11, 1, 6, 30)
    assert gna._ny_date_str(fall_back_first) == "2026-11-01"
    assert gna._ny_date_str(fall_back_second) == "2026-11-01"

    # NY-midnight rollover: 03:00 UTC on Jan 1 = 22:00 ET previous day.
    midnight_utc = _utc(2026, 1, 1, 3, 0)
    assert gna._ny_date_str(midnight_utc) == "2025-12-31"


def test_pre_session_does_not_write_marker():
    """4 AM PT tick (11 UTC during PDT) → pre-session → return empty dict,
    marker NOT written. Note: 2026-05-06 dual-cadence redesign — 5-6:30
    AM PT now runs MONITOR (still no marker write since marker is gated
    on FULL cycle completion); only <5 AM PT returns empty."""
    strat = _strategy()
    config = _live_config()
    cache = {}
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 11, 0),  # 4 AM PT (PDT)
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    assert out == {}
    assert "_nexus_full_cycle_completed_date" not in cache


def test_pre_market_monitor_does_not_write_marker():
    """5:30 AM PT (12:30 UTC PDT) → pre-market MONITOR cycle runs but the
    full-cycle marker is NOT written (marker is only set after a successful
    FULL pipeline completion, never during MONITOR)."""
    strat = _strategy()
    config = _live_config()
    cache = {}
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 12, 30),  # 5:30 AM PT
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    # Monitor produces a non-empty dict (with metadata).
    assert isinstance(out, dict)
    assert "_nexus_discovered" in out
    # Marker NOT written despite gate firing — MONITOR mode.
    assert "_nexus_full_cycle_completed_date" not in cache
