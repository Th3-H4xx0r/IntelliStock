"""
Dual-cadence gate tests.

The gate sits in run_once between date_key computation and the Discord
alert block. Three states:
  - Pre-open ET: returns {} early (no work, no alert).
  - First post-open tick + no marker: full pipeline runs.
  - Post-open tick + marker == today: monitor cycle runs.

Backtest preservation: gate is a no-op when _GN_LIVE_MODE_FLAG=False or
historical_lookback_mode=True.

Sub-hourly cadence: gate refuses to activate (yellow log fires once,
falls through to full pipeline every tick).
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_live_mode_flag():
    """Each test starts with the live-mode flag cleared."""
    gna._GN_LIVE_MODE_FLAG = False
    yield
    gna._GN_LIVE_MODE_FLAG = False


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# State machine probes (call run_once with patches that short-circuit
# the heavy work after the gate fires)
# ---------------------------------------------------------------------------


def _make_strategy():
    return gna.GraphNexusAnalysis()


def test_pre_session_returns_empty_dict():
    """4 AM PT (11 UTC during PDT) → before session_open=5 AM PT → return empty dict."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    cache = {}
    # 11:00 UTC during PDT (UTC-7) = 4:00 AM PT, before 5 AM PT session open.
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 11, 0),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    assert out == {}
    # Marker NOT written.
    assert "_nexus_full_cycle_completed_date" not in cache


def test_pre_market_runs_monitor():
    """5:30 AM PT (12:30 UTC during PDT) → after open=5 AM PT, before
    full_anchor=6:30 AM PT → MONITOR cycle runs (NOT full pipeline)."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    cache = {}
    # 12:30 UTC PDT = 5:30 AM PT, in pre-market window.
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 12, 30),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    # Monitor output shape — has metadata keys, NOT the empty {} of pre-session.
    assert isinstance(out, dict)
    assert "_nexus_discovered" in out
    # Pre-market monitor must NOT write the full-cycle marker.
    assert "_nexus_full_cycle_completed_date" not in cache


def test_post_open_with_today_marker_runs_monitor():
    """11 AM ET tick + today marker → monitor cycle runs (no full pipeline)."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    # 15:00 UTC during EDT = 11:00 AM ET = 8 AM PT, post-full-anchor.
    now = _utc(2026, 5, 1, 15, 0)
    cache = {"_nexus_full_cycle_completed_date": "2026-05-01"}
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=now,
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    # Monitor returns its dict shape (with metadata keys), and the
    # full-cycle marker is unchanged. portfolio_emulator=None means no
    # symbol entries — only the metadata keys.
    assert isinstance(out, dict)
    assert "_nexus_discovered" in out
    assert out["_nexus_discovered"] == []
    assert cache.get("_nexus_full_cycle_completed_date") == "2026-05-01"


def test_backtest_mode_gate_is_noop():
    """_GN_LIVE_MODE_FLAG=False → gate inactive; no early return."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": False,  # backtest
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    cache = {}
    # Pre-open in NY, but backtest mode means gate is inactive.
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 12, 0),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    # Gate didn't return {} early; full pipeline ran (returned dict may
    # be empty for empty symbols, but it should not be the gate {}).
    # Marker should NOT be written (gate inactive in backtest).
    assert "_nexus_full_cycle_completed_date" not in cache


def test_lookback_mode_gate_is_noop():
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "historical_lookback_mode": True,  # lookback
        "use_llm_sentiment": False,
    }
    cache = {}
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 12, 0),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    # Marker NOT written during lookback.
    assert "_nexus_full_cycle_completed_date" not in cache


def test_subhourly_cadence_refuses_activation():
    """time_increment=10 → below 30s sanity floor; gate refuses; warns once.
    Note: 2026-05-06 lowered the threshold from 3600s to 30s so 20-min
    (1200s) ticks are now supported. Only pathological sub-30s ticks refuse."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    cache = {}
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 15, 0),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=10,  # below 30s sanity floor
    )
    # Warning written.
    assert cache.get("_nexus_dual_cadence_warned_subhour") is True
    # Marker NOT written (full pipeline runs, but gate didn't write
    # marker because dual_cadence_active was set False).
    assert "_nexus_full_cycle_completed_date" not in cache


def test_20min_cadence_activates_dual_cadence():
    """time_increment=1200 (20 min) → above 30s floor; gate ACTIVATES."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    cache = {}
    # 15:00 UTC during EDT = 8:00 AM PT, post-full-anchor (6:30 AM PT).
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 15, 0),
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=1200,  # 20 min — well above 30s floor
    )
    # Sub-hour warning NOT written.
    assert cache.get("_nexus_dual_cadence_warned_subhour") is None


def test_naive_current_time_does_not_crash():
    """Gate's defensive _ensure_utc handling should tolerate naive datetimes."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "use_llm_sentiment": False,
    }
    cache = {}
    naive = datetime(2026, 5, 1, 12, 0)  # NO tzinfo
    # Should not raise.
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=naive,
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    # Result is some dict (possibly {} if pre-open).
    assert isinstance(out, dict)


def test_marker_skipped_when_dual_cadence_disabled():
    """nexus_dual_cadence_enabled=False → marker never written."""
    strat = _make_strategy()
    config = {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": False,  # opt out
        "use_llm_sentiment": False,
    }
    cache = {}
    out = strat.run_once(
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
