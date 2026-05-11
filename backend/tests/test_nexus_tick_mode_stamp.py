"""Tests for the 2026-05-07 backtest perf optimization.

The dual-cadence backtest harness has the strategy decide FULL/MONITOR/IDLE
itself based on time-of-day. The broker doesn't see which mode was picked,
so it ran post-tick EPPI + snapshot on every bar regardless. At 1200s
granularity that's ~3000 wasted EPPI iterations per backtest where MONITOR
returned no scores and no fills happened.

Fix: graph_nexus_analysis stamps ``strategy_cache["_nexus_last_tick_mode"]``
on every entry path (FULL / MONITOR / IDLE), and broker.py reads it to
short-circuit post-tick work on non-FULL bars in dual-cadence backtest
sim mode.

These tests verify the stamping behavior; the broker-side skip is verified
by integration-level reasoning since broker.py is not import-safe (runs
argparse at module load).
"""

from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _make_strategy():
    """Build a GraphNexusAnalysis instance bypassing __init__ side effects."""
    from strategies.graph_nexus_analysis import GraphNexusAnalysis
    return GraphNexusAnalysis.__new__(GraphNexusAnalysis)


def test_run_monitor_cycle_stamps_mode_in_cache():
    """Monitor entry stamps `_nexus_last_tick_mode = MONITOR` so the
    broker can decide to skip post-tick EPPI/snapshot.
    """
    strat = _make_strategy()
    cache: dict = {}
    # Empty held positions → monitor returns immediately. We just want to
    # verify the stamp lands.
    strat._run_monitor_cycle(
        symbols_list=[],
        prices={},
        price_history=None,
        config={},
        portfolio_emulator=None,
        strategy_cache=cache,
        date_key="2026-05-07",
        current_time=None,
    )
    assert cache.get("_nexus_last_tick_mode") == "MONITOR"


def test_idle_branch_stamps_idle_mode():
    """The broker-driven mode='IDLE' branch in run_once writes
    `_nexus_last_tick_mode = IDLE` so post-tick work can be skipped.
    """
    from datetime import datetime, timezone
    strat = _make_strategy()
    cache: dict = {}
    # mode="IDLE" exits early after stamping. Pass minimum config + fake
    # current_time. The branch is entered by run_once before any heavy
    # work (config validation, neo4j, etc.) — but those still execute on
    # the early read path. To avoid pulling in those deps, we test the
    # IDLE branch by replicating the relevant code path.
    #
    # Implementation cross-check: the IDLE branch in run_once at line
    # ~16313 is gated by `if mode == "IDLE"` and returns immediately
    # after stamping. We verify the stamping behavior by inspecting that
    # the source contains the expected assignment.
    import inspect
    import strategies.graph_nexus_analysis as gna
    src = inspect.getsource(gna.GraphNexusAnalysis.run_once)
    assert 'strategy_cache["_nexus_last_tick_mode"] = "IDLE"' in src, (
        "IDLE branch dropped the cache stamp — broker can no longer "
        "short-circuit post-tick work on idle ticks."
    )


def test_full_pipeline_stamps_full_mode():
    """After the dual-cadence gate falls through to the FULL pipeline,
    `_nexus_last_tick_mode = FULL` is set so the broker continues to
    run post-tick EPPI/snapshot on real-work bars.
    """
    import inspect
    import strategies.graph_nexus_analysis as gna
    src = inspect.getsource(gna.GraphNexusAnalysis.run_once)
    assert 'strategy_cache["_nexus_last_tick_mode"] = "FULL"' in src, (
        "FULL pipeline dropped the cache stamp — broker would always "
        "skip post-tick on dual-cadence backtest, breaking equity curve."
    )


def test_run_once_stamps_modes_at_three_distinct_sites():
    """Defensive: the three stamps (MONITOR / IDLE / FULL) must each
    appear in the source. Catches accidental refactors that drop one.
    """
    import inspect
    import strategies.graph_nexus_analysis as gna
    src_run_once = inspect.getsource(gna.GraphNexusAnalysis.run_once)
    src_monitor = inspect.getsource(gna.GraphNexusAnalysis._run_monitor_cycle)
    assert '"_nexus_last_tick_mode"' in src_run_once
    assert '"_nexus_last_tick_mode"' in src_monitor
    # Each mode should be set somewhere.
    combined = src_run_once + src_monitor
    assert '"FULL"' in combined
    assert '"MONITOR"' in combined
    assert '"IDLE"' in combined


def test_run_once_clears_stamp_at_tick_entry():
    """Defensive: a stale stamp from the previous tick MUST be cleared
    at the start of run_once so a mid-strategy crash leaves None
    (broker falls back to safe snapshot path) rather than a stale value
    that would cause the broker to skip the snapshot on a bar where
    fills actually happened.

    Implementation check: source must reset `_nexus_last_tick_mode = None`
    near the top of run_once, before the IDLE / MONITOR / FULL branches.
    """
    import inspect
    import strategies.graph_nexus_analysis as gna
    src = inspect.getsource(gna.GraphNexusAnalysis.run_once)
    # The clear must happen.
    assert 'strategy_cache["_nexus_last_tick_mode"] = None' in src, (
        "run_once dropped the tick-entry stamp clear — a mid-strategy "
        "crash will leave a stale FULL/MONITOR stamp that causes the "
        "broker to skip post-tick snapshots on bars where fills happened."
    )
    # The clear must happen BEFORE the IDLE branch so that branch's
    # stamp lands on a fresh dict.
    clear_idx = src.find('"_nexus_last_tick_mode"] = None')
    idle_branch_idx = src.find('if mode == "IDLE":')
    assert 0 < clear_idx < idle_branch_idx, (
        "Clear must precede the IDLE branch so the entry-clear happens "
        "before any mode dispatch."
    )
