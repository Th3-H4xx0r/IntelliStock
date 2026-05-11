"""
Dual-cadence backtest harness wiring tests.

Spec: docs/superpowers/specs/2026-05-02-nexus-dual-cadence-backtest-harness-design.md

The harness opts a backtest into the same dual-cadence gate that live mode uses
so operators can validate WIRING (gate routing, marker rollover, day-trading
guard, monitor cycle correctness) on real historical bars. It is NOT a P&L A/B
vs the daily-cadence +49% baseline; the spec is explicit on that.

These tests cover:
  - Gate routing in backtest mode with the harness flag on.
  - Coupled-flag enforcement (raises ValueError at run_once entry).
  - Backtest baseline preservation when the flag is absent.
  - NY-midnight rollover triggers a new full cycle on the next NY day.
  - Pre-flight checks (granularity / hourly-bar coverage) raise SystemExit.
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
    """Each test starts with the live-mode flag cleared (backtest semantics)."""
    gna._GN_LIVE_MODE_FLAG = False
    yield
    gna._GN_LIVE_MODE_FLAG = False


def _strategy():
    return gna.GraphNexusAnalysis()


def _harness_config():
    """Backtest config with the harness flag on (no _nexus_is_live_mode)."""
    return {
        "_nexus_is_live_mode": False,
        "nexus_dual_cadence_enabled": True,
        "nexus_dual_cadence_backtest_simulation": True,
        "use_llm_sentiment": False,
    }


# ---------------------------------------------------------------------------
# 1) Coupled-flag enforcement (raises ValueError)
# ---------------------------------------------------------------------------


def test_coupled_flag_enforcement_raises():
    """nexus_dual_cadence_backtest_simulation=True + nexus_dual_cadence_enabled=False
    must raise ValueError before any work runs."""
    strat = _strategy()
    config = {
        "_nexus_is_live_mode": False,
        "nexus_dual_cadence_enabled": False,
        "nexus_dual_cadence_backtest_simulation": True,
        "use_llm_sentiment": False,
    }
    with pytest.raises(ValueError, match="nexus_dual_cadence_backtest_simulation"):
        strat.run_once(
            symbols=[],
            prices={},
            current_time=_utc(2026, 5, 1, 15, 0),
            config=config,
            conditions={},
            portfolio_emulator=None,
            strategy_cache={},
            time_increment=3600,
        )


# ---------------------------------------------------------------------------
# 2) Backtest baseline preservation (flag off = no behavior change)
# ---------------------------------------------------------------------------


def test_baseline_preservation_when_flag_off():
    """Default config (flag absent / False) -> gate inactive in backtest;
    marker not written; monitor not called; behavior identical to today."""
    strat = _strategy()
    config = {
        "_nexus_is_live_mode": False,  # backtest
        "nexus_dual_cadence_enabled": True,
        # nexus_dual_cadence_backtest_simulation absent -> defaults to False
        "use_llm_sentiment": False,
    }
    cache = {}
    monitor_calls = [0]
    original_monitor = strat._run_monitor_cycle

    def _spy_monitor(*args, **kwargs):
        monitor_calls[0] += 1
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
    except (ImportError, RuntimeError, ConnectionError):
        pass  # No-DB test env may abort the full pipeline; gate routing is what we care about.
    # Gate inactive in backtest baseline -> marker never written, monitor never called.
    assert "_nexus_full_cycle_completed_date" not in cache
    assert monitor_calls[0] == 0


def test_baseline_preservation_explicit_false():
    """Explicit nexus_dual_cadence_backtest_simulation=False also preserves baseline."""
    strat = _strategy()
    config = {
        "_nexus_is_live_mode": False,
        "nexus_dual_cadence_enabled": True,
        "nexus_dual_cadence_backtest_simulation": False,
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


# ---------------------------------------------------------------------------
# 3) Gate routing in backtest mode with harness flag on
# ---------------------------------------------------------------------------


def test_pre_session_returns_empty_with_harness_flag():
    """4 AM PT (11 UTC PDT) + harness flag on -> pre-session early return ({}).
    Note: 2026-05-06 dual-cadence redesign — pre-session is now <5 AM PT
    (was <9:30 AM ET)."""
    strat = _strategy()
    config = _harness_config()
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


def test_post_open_with_today_marker_routes_to_monitor_in_backtest():
    """11 AM ET tick + today's marker + harness flag on -> monitor cycle runs.
    Spy on _run_monitor_cycle to PROVE the gate routes there (an isinstance(out, dict)
    check would pass even if the full pipeline ran instead)."""
    strat = _strategy()
    config = _harness_config()
    cache = {"_nexus_full_cycle_completed_date": "2026-05-01"}

    monitor_calls = [0]
    original_monitor = strat._run_monitor_cycle

    def _spy_monitor(*args, **kwargs):
        monitor_calls[0] += 1
        return original_monitor(*args, **kwargs)

    strat._run_monitor_cycle = _spy_monitor
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 1, 15, 0),  # 11 AM ET
        config=config,
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=3600,
    )
    # Critical: the gate MUST have routed to monitor exactly once.
    assert monitor_calls[0] == 1, "Gate should route to monitor on date match"
    # Monitor cycle returns its dict shape with metadata keys.
    assert isinstance(out, dict)
    assert "_nexus_discovered" in out
    assert out["_nexus_discovered"] == []
    # Marker unchanged.
    assert cache["_nexus_full_cycle_completed_date"] == "2026-05-01"


# ---------------------------------------------------------------------------
# 4) NY-midnight rollover triggers a new full cycle on the next NY day
# ---------------------------------------------------------------------------


def test_ny_midnight_rollover_triggers_new_full_cycle():
    """A tick whose NY date differs from the marker must NOT route to monitor —
    the gate falls through to the full pipeline so the new day's marker can be
    written. We spy on _run_monitor_cycle to confirm it is NOT called."""
    strat = _strategy()
    config = _harness_config()
    # Marker says yesterday; current tick is post-open today.
    cache = {"_nexus_full_cycle_completed_date": "2026-04-30"}

    monitor_calls = [0]
    original_monitor = strat._run_monitor_cycle

    def _spy_monitor(*args, **kwargs):
        monitor_calls[0] += 1
        return original_monitor(*args, **kwargs)

    strat._run_monitor_cycle = _spy_monitor
    try:
        strat.run_once(
            symbols=[],
            prices={},
            current_time=_utc(2026, 5, 1, 15, 0),  # 11 AM ET, new NY day
            config=config,
            conditions={},
            portfolio_emulator=None,
            strategy_cache=cache,
            time_increment=3600,
        )
    except (ImportError, RuntimeError, ConnectionError):
        # No-DB test env may abort the full pipeline; we only assert that
        # monitor was NOT called on the date-mismatch. ValueError and
        # AssertionError are NOT caught here so a misfire of the coupled-flag
        # validator or any in-test assertion fails the test loudly.
        pass
    assert monitor_calls[0] == 0, "Monitor must NOT run when marker date != today"


# ---------------------------------------------------------------------------
# 5) Pre-flight checks (broker-level helper, called from broker.py:~4313)
# ---------------------------------------------------------------------------


def test_preflight_rejects_above_3600_granularity():
    """granularity_sec > 3600 -> rejected (daily would never activate gate).

    2026-05-07 scheduler refactor: relaxed from `== 3600` to `[30, 3600]`,
    so sub-hourly cadences (e.g. 900s = 15min) are now allowed for harness
    runs at finer granularity.
    """
    from dual_cadence_preflight import dual_cadence_backtest_preflight as _dual_cadence_backtest_preflight  # noqa: WPS433

    from dual_cadence_preflight import DualCadencePreflightError  # noqa: WPS433

    with pytest.raises(DualCadencePreflightError, match=r"\[30, 3600\]"):
        _dual_cadence_backtest_preflight(data={}, time_increment=86400, symbols=["AAPL"])


def test_preflight_rejects_below_30_granularity():
    """granularity_sec < 30 -> rejected (sub-30s outruns persistence)."""
    from dual_cadence_preflight import dual_cadence_backtest_preflight as _dual_cadence_backtest_preflight  # noqa: WPS433

    from dual_cadence_preflight import DualCadencePreflightError  # noqa: WPS433

    with pytest.raises(DualCadencePreflightError, match=r"\[30, 3600\]"):
        _dual_cadence_backtest_preflight(data={}, time_increment=10, symbols=["AAPL"])


def test_preflight_accepts_subhourly_granularity():
    """900s (15-min) is now accepted (was rejected pre-2026-05-07)."""
    from dual_cadence_preflight import dual_cadence_backtest_preflight as _dual_cadence_backtest_preflight  # noqa: WPS433

    # No data, no symbols → no coverage check fires; should return cleanly.
    _dual_cadence_backtest_preflight(data={}, time_increment=900, symbols=[])


def test_preflight_rejects_non_numeric_granularity():
    """Non-numeric string time_increment is rejected with the new error msg."""
    from dual_cadence_preflight import dual_cadence_backtest_preflight as _dual_cadence_backtest_preflight  # noqa: WPS433

    from dual_cadence_preflight import DualCadencePreflightError  # noqa: WPS433

    with pytest.raises(DualCadencePreflightError, match=r"\[30, 3600\]"):
        _dual_cadence_backtest_preflight(data={}, time_increment="not-a-number", symbols=["AAPL"])


def test_preflight_rth_filter_drops_extended_hours_bars():
    """Bars outside 9:30-16:00 ET must be dropped from data[symbol]."""
    from dual_cadence_preflight import dual_cadence_backtest_preflight as _dual_cadence_backtest_preflight  # noqa: WPS433

    # Build bars: 04:00 UTC (premarket EST), 14:30 UTC (9:30 ET = first RTH),
    # 20:30 UTC (15:30 ET = last RTH hourly), 22:00 UTC (after-hours).
    data = {
        "AAPL": [
            {"t": "2026-05-01T04:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T14:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T15:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T20:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T22:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
        ]
    }
    # Coverage check would fail (only 3 RTH bars vs expected 7), so we bypass
    # that by passing symbols=[] (no active symbols to coverage-check). The
    # RTH filter still runs over data.keys().
    _dual_cadence_backtest_preflight(data=data, time_increment=3600, symbols=[])
    kept_ts = {b["t"] for b in data["AAPL"]}
    # 14:30 UTC = 10:30 ET (RTH), 15:30 UTC = 11:30 ET (RTH),
    # 20:30 UTC = 16:30 ET (POST-RTH; hour=16 not in 10..15) — dropped.
    # 04:00 UTC = 00:00 ET (PRE), 22:00 UTC = 18:00 ET (POST) — dropped.
    assert "2026-05-01T14:30:00Z" in kept_ts
    assert "2026-05-01T15:30:00Z" in kept_ts
    assert "2026-05-01T04:00:00Z" not in kept_ts
    assert "2026-05-01T20:30:00Z" not in kept_ts
    assert "2026-05-01T22:00:00Z" not in kept_ts


def test_preflight_rejects_low_hourly_coverage():
    """Active symbol with <90% RTH-hourly coverage -> SystemExit."""
    from dual_cadence_preflight import dual_cadence_backtest_preflight as _dual_cadence_backtest_preflight  # noqa: WPS433

    # One trading day in the data set (so expected = 7 RTH bars).
    # Provide only 4 RTH bars for AAPL -> 4/7 = 57% coverage -> reject.
    data = {
        "AAPL": [
            {"t": "2026-05-01T14:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T15:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T16:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T17:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
        ]
    }
    from dual_cadence_preflight import DualCadencePreflightError  # noqa: WPS433

    with pytest.raises(DualCadencePreflightError, match="below 90% RTH-bar coverage"):
        _dual_cadence_backtest_preflight(data=data, time_increment=3600, symbols=["AAPL"])


def test_preflight_skips_zero_bar_symbols():
    """Symbols with zero bars (likely delisted) are skipped — broker has its
    own logic for those. The pre-flight should NOT raise on them."""
    from dual_cadence_preflight import dual_cadence_backtest_preflight as _dual_cadence_backtest_preflight  # noqa: WPS433

    # AAPL has full coverage: 7 RTH-hourly bars on one trading day.
    # NY-EDT is UTC-4 in May, so RTH 9:30-15:30 ET = 13:30-19:30 UTC.
    # 13:30 UTC = 09:30 ET (kept by hh==9 and mm>=30 branch).
    # 14:30..19:30 UTC = 10:30..15:30 ET (kept by 10<=hh<=15 branch).
    data = {
        "AAPL": [
            {"t": "2026-05-01T13:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T14:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T15:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T16:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T17:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T18:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": "2026-05-01T19:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
        ],
        "DELISTED": [],
    }
    # No exception expected.
    _dual_cadence_backtest_preflight(
        data=data, time_increment=3600, symbols=["AAPL", "DELISTED"]
    )


# ---------------------------------------------------------------------------
# 6) Coupled-flag validator at backtest startup (broker-side)
# ---------------------------------------------------------------------------


def test_validate_coupled_flags_raises_on_misconfig():
    """validate_coupled_flags should raise at backtest startup if any spec
    has nexus_dual_cadence_backtest_simulation=True with
    nexus_dual_cadence_enabled=False. Fires BEFORE the tick loop so the
    operator sees a clear error instead of per-tick log spam."""
    from dual_cadence_preflight import (
        DualCadencePreflightError,
        validate_coupled_flags,
    )

    bad_specs = [
        {"strategy": "graph_nexus_analysis", "config": {
            "nexus_dual_cadence_backtest_simulation": True,
            "nexus_dual_cadence_enabled": False,
        }},
    ]
    with pytest.raises(DualCadencePreflightError, match="requires nexus_dual_cadence_enabled=True"):
        validate_coupled_flags(bad_specs)


def test_validate_coupled_flags_accepts_valid_configs():
    """No raise when both flags are True or when backtest_simulation is False."""
    from dual_cadence_preflight import validate_coupled_flags

    # Both True - valid harness config.
    validate_coupled_flags([
        {"strategy": "graph_nexus_analysis", "config": {
            "nexus_dual_cadence_backtest_simulation": True,
            "nexus_dual_cadence_enabled": True,
        }}
    ])
    # backtest_simulation False - validator doesn't care about enabled.
    validate_coupled_flags([
        {"strategy": "graph_nexus_analysis", "config": {
            "nexus_dual_cadence_backtest_simulation": False,
            "nexus_dual_cadence_enabled": False,
        }}
    ])
    # No specs - no-op.
    validate_coupled_flags([])
    validate_coupled_flags(None)


def test_preflight_max_pages_constant_present_in_broker():
    """Sanity check that the pagination cap (CRITICAL fix) is present in
    fetch_alpaca_historical_bars. Without this, a buggy Alpaca response
    returning next_page_token forever would freeze the fetch thread."""
    import os
    broker_path = os.path.join(os.path.dirname(__file__), "..", "broker.py")
    with open(broker_path, "r", encoding="utf-8") as f:
        body = f.read()
    assert "_max_pages = 500" in body
    assert "Alpaca pagination cap hit" in body
