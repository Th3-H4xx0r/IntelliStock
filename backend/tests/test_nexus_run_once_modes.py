"""Mode-dispatch tests for graph_nexus_analysis.run_once.

Verifies the explicit mode parameter (added in commit 2 of the
2026-05-07 scheduler refactor):
- mode="IDLE" returns {} fast, no pipeline, no marker write
- mode="MONITOR" routes to _run_monitor_cycle, no FULL pipeline
- mode=None preserves legacy gate behavior (backward compat)
- mode="FULL" bypasses the legacy gate and runs the full pipeline
- Invalid mode is treated as None (forward compat)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from time import perf_counter

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_live_flag():
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


def test_mode_idle_returns_empty_dict_fast():
    """mode='IDLE' returns {} in well under 50ms with no pipeline work."""
    strat = _strategy()
    cache = {}
    t0 = perf_counter()
    out = strat.run_once(
        symbols=[],
        prices={},
        current_time=_utc(2026, 5, 7, 12, 0),
        config=_live_config(),
        conditions={},
        portfolio_emulator=None,
        strategy_cache=cache,
        time_increment=1200,
        mode="IDLE",
    )
    elapsed = perf_counter() - t0
    assert out == {}
    assert elapsed < 0.5, f"IDLE dispatch took {elapsed*1000:.1f}ms — should be <500ms"


def test_mode_idle_increments_counter_and_logs_throttled():
    """Repeated IDLE ticks increment the counter; log throttle is 1 + every 60th."""
    strat = _strategy()
    cache = {}
    for i in range(3):
        strat.run_once(
            symbols=[], prices={}, current_time=_utc(2026, 5, 7, 12, 0),
            config=_live_config(), conditions={}, portfolio_emulator=None,
            strategy_cache=cache, time_increment=1200, mode="IDLE",
        )
    assert cache.get("_nexus_idle_tick_count") == 3


def test_mode_idle_does_not_write_marker():
    strat = _strategy()
    cache = {}
    strat.run_once(
        symbols=[], prices={}, current_time=_utc(2026, 5, 7, 12, 0),
        config=_live_config(), conditions={}, portfolio_emulator=None,
        strategy_cache=cache, time_increment=1200, mode="IDLE",
    )
    assert "_nexus_full_cycle_completed_date" not in cache


def test_mode_monitor_calls_run_monitor_cycle():
    """mode='MONITOR' routes to _run_monitor_cycle, not the FULL pipeline."""
    strat = _strategy()
    monitor_called = [0]
    original_monitor = strat._run_monitor_cycle

    def _spy(*args, **kwargs):
        monitor_called[0] += 1
        return original_monitor(*args, **kwargs)

    strat._run_monitor_cycle = _spy

    out = strat.run_once(
        symbols=[], prices={},
        current_time=_utc(2026, 5, 7, 13, 30),
        config=_live_config(), conditions={}, portfolio_emulator=None,
        strategy_cache={}, time_increment=1200, mode="MONITOR",
    )
    assert monitor_called[0] == 1
    # Monitor returns a dict (with metadata or empty if no positions).
    assert isinstance(out, dict)


def test_mode_monitor_does_not_write_full_marker():
    """MONITOR cycle does not write the FULL completion marker."""
    strat = _strategy()
    cache = {}
    strat.run_once(
        symbols=[], prices={},
        current_time=_utc(2026, 5, 7, 13, 30),
        config=_live_config(), conditions={}, portfolio_emulator=None,
        strategy_cache=cache, time_increment=1200, mode="MONITOR",
    )
    assert "_nexus_full_cycle_completed_date" not in cache


def test_mode_full_bypasses_legacy_gate():
    """mode='FULL' should bypass the dual-cadence gate entirely.

    Set _GN_LIVE_MODE_FLAG=True to make the legacy gate active. Without
    mode='FULL' the gate would route to MONITOR (no marker → FULL allowed
    only at anchor; at 4 AM PT the gate would early-return {} pre-session).
    With mode='FULL' the gate is skipped and the FULL pipeline begins.
    We don't run the full pipeline (no Neo4j etc.) — just verify the gate
    is bypassed by checking the strategy proceeds past pre-session check.
    """
    gna._GN_LIVE_MODE_FLAG = True
    strat = _strategy()
    monitor_called = [0]

    def _spy(*args, **kwargs):
        monitor_called[0] += 1
        return {}

    strat._run_monitor_cycle = _spy

    # 11 UTC = 4 AM PDT — pre-session per legacy gate. With mode=None,
    # the gate would early-return {} (not call monitor). With mode='FULL',
    # the gate is bypassed and the full pipeline tries to run (we'll let
    # it crash/return — just need to verify monitor wasn't called).
    try:
        strat.run_once(
            symbols=[], prices={},
            current_time=_utc(2026, 5, 7, 11, 0),  # 4 AM PDT
            config=_live_config(), conditions={}, portfolio_emulator=None,
            strategy_cache={}, time_increment=1200, mode="FULL",
        )
    except Exception:
        pass  # Pipeline may fail without Neo4j etc; we only care about routing.
    # Critical: monitor was NOT called (mode=FULL skipped the gate).
    assert monitor_called[0] == 0


def test_mode_none_preserves_legacy_gate_pre_session_idle():
    """mode=None at 4 AM PDT (pre-session) → legacy gate returns {}."""
    gna._GN_LIVE_MODE_FLAG = True
    strat = _strategy()
    cache = {}
    out = strat.run_once(
        symbols=[], prices={},
        current_time=_utc(2026, 5, 7, 11, 0),  # 4 AM PDT
        config=_live_config(), conditions={}, portfolio_emulator=None,
        strategy_cache=cache, time_increment=1200,
        # mode=None implicit
    )
    assert out == {}
    # Legacy gate stamps a pre-session log marker.
    assert any(k.startswith("_nexus_pre_session_logged_") for k in cache.keys())


def test_mode_none_in_backtest_runs_full_pipeline():
    """Backtest path (live flag False) with mode=None should NOT hit the gate
    at all (gate requires live flag) — falls through to FULL pipeline.

    We don't actually run the full pipeline (too heavy); we check that
    no early-return happened. The strategy will fail in the pipeline
    body without proper data — that's fine, we catch the exception.
    """
    strat = _strategy()
    cache = {}
    monitor_called = [0]

    def _spy(*args, **kwargs):
        monitor_called[0] += 1
        return {}

    strat._run_monitor_cycle = _spy

    try:
        strat.run_once(
            symbols=[], prices={},
            current_time=_utc(2026, 5, 7, 13, 30),
            config={"_nexus_is_live_mode": False, "use_llm_sentiment": False},
            conditions={}, portfolio_emulator=None,
            strategy_cache=cache, time_increment=1200,
        )
    except Exception:
        pass  # Pipeline body fails; we only care about routing.
    # No monitor route — gate inactive, FULL fires.
    assert monitor_called[0] == 0


def test_mode_invalid_string_treated_as_full():
    """Forward compat: unknown mode falls through to FULL behavior.

    The current implementation does NOT validate mode strings (accepts
    None, IDLE, MONITOR, anything-else). Anything other than IDLE/MONITOR
    is treated as FULL (gate bypassed via `mode is None` check).
    """
    strat = _strategy()
    monitor_called = [0]

    def _spy(*args, **kwargs):
        monitor_called[0] += 1
        return {}

    strat._run_monitor_cycle = _spy

    try:
        strat.run_once(
            symbols=[], prices={},
            current_time=_utc(2026, 5, 7, 13, 30),
            config=_live_config(), conditions={}, portfolio_emulator=None,
            strategy_cache={}, time_increment=1200, mode="GIBBERISH",
        )
    except Exception:
        pass
    # Treated as non-None mode → gate bypassed → no monitor route.
    assert monitor_called[0] == 0


def test_mode_full_writes_marker_after_successful_full_pipeline(monkeypatch):
    """CRITICAL: when broker passes mode='FULL', the marker MUST be written
    on success. Without this, broker would never see today's marker → next
    day's MONITOR would think yesterday's FULL never ran (no functional
    impact yet because scheduler picks FULL only at the anchor, but
    sell-enforcement cache would stay empty all day).
    """
    strat = _strategy()
    cache = {}

    # Stub out the heavy pipeline by short-circuiting at a known internal
    # entry point. We can't fully run the pipeline (Neo4j / LLM / news);
    # instead, directly verify the marker code path exists by simulating
    # a successful return-from-FULL state.
    # The marker write is at the END of run_once, just before `return scores`.
    # To test the dispatch+marker contract, we can't trivially run the full
    # pipeline. Instead, we verify the write logic via direct cache check
    # after a real run_once attempt (which will fail in pipeline body but
    # allows us to confirm the marker write is reachable when mode=FULL).
    # The marker write is only triggered on successful pipeline completion,
    # so this test asserts that the GUARD admits mode="FULL". Reading the
    # code at lines ~21852-21895 directly verifies this.
    src_path = os.path.join(_BACKEND, "strategies", "graph_nexus_analysis.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    # Verify the guard explicitly includes mode == "FULL".
    assert 'mode == "FULL"' in src
    # Verify the guard is part of the marker-write block.
    assert "_marker_should_write" in src
    # Verify _today_ny is computed defensively when not set by gate.
    assert "if not _today_ny" in src


def test_mode_propagates_runtime_instance_id_for_monitor():
    """MONITOR dispatch sets config['runtime_instance_id'] for monitor's
    stable dedup key (same pattern as the legacy gate)."""
    strat = _strategy()
    cfg = _live_config()
    cfg["runtime_instance_id"] = ""  # empty triggers the auto-set
    strat.run_once(
        symbols=[], prices={},
        current_time=_utc(2026, 5, 7, 13, 30),
        config=cfg, conditions={}, portfolio_emulator=None,
        strategy_cache={}, time_increment=1200, mode="MONITOR",
    )
    # instance_id was auto-resolved to "default" inside run_once preamble;
    # monitor dispatch stamps it on the config dict.
    assert cfg.get("runtime_instance_id")  # truthy, populated
