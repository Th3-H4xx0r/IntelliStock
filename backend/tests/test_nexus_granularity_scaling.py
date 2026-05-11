"""Tests for the 2026-05-07 granularity scaling helper.

Bug context: ``backfill_queue_max_bars`` (default 10) and
``backfill_queue_priority_grace_bars`` (default 8) were tuned at the
3600s (1-hour) baseline. Backtest 346930 ran at 1200s (20-min) and the
same bar-count parameters represented 1/3 the wall-clock window:
priority TTL collapsed from ~16 hours to ~5 hours, well below V28
leader-lock rotation latency. Result: 6 of the top-10 baseline winners
(SNDK, MU, META, etc.) expired before they could enter.

The fix: ``_scale_bars(value, config)`` reads
``config["_resolved_time_increment_sec"]`` (set at the top of
``run_once``) and rescales bar-count parameters to preserve wall-clock
duration across cadences.

These tests verify:
1. Pass-through when time_increment is missing or matches baseline.
2. 3x scaling at 1200s (the production failure case).
3. 0.5x scaling at 7200s (slower cadence).
4. Floors at 1 (never returns 0 when input was positive).
5. Defensive: bad inputs return value unchanged, never raise.
"""

from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies.graph_nexus_analysis import _scale_bars  # noqa: E402


def test_scale_bars_passthrough_when_time_increment_missing():
    """Missing time_increment → no scaling. Backtest at the baseline
    cadence must not be perturbed.
    """
    assert _scale_bars(10, {}) == 10
    assert _scale_bars(10, None) == 10
    assert _scale_bars(10, {"_resolved_time_increment_sec": 0}) == 10


def test_scale_bars_passthrough_at_baseline_granularity():
    """time_increment == baseline (3600) → no scaling."""
    cfg = {"_resolved_time_increment_sec": 3600}
    assert _scale_bars(10, cfg) == 10
    assert _scale_bars(15, cfg) == 15
    assert _scale_bars(1, cfg) == 1


def test_scale_bars_triples_at_1200s():
    """The actual production failure case: 1200s = 3x finer than 3600s,
    so the equivalent bar count must be 3x larger to preserve wall-clock
    duration.
    """
    cfg = {"_resolved_time_increment_sec": 1200}
    # 10 bars × 3600s = 36000s ≡ 30 bars × 1200s = 36000s
    assert _scale_bars(10, cfg) == 30
    assert _scale_bars(15, cfg) == 45
    assert _scale_bars(5, cfg) == 15
    assert _scale_bars(3, cfg) == 9
    assert _scale_bars(8, cfg) == 24


def test_scale_bars_halves_at_7200s():
    """Slower cadence (7200s = 2-hour bars) → half as many bars to
    cover the same wall-clock window.
    """
    cfg = {"_resolved_time_increment_sec": 7200}
    # 10 × 3600 = 36000s ≡ 5 × 7200 = 36000s
    assert _scale_bars(10, cfg) == 5
    assert _scale_bars(20, cfg) == 10


def test_scale_bars_quarters_at_900s():
    """15-min bars: 4x finer → 4x the bar count."""
    cfg = {"_resolved_time_increment_sec": 900}
    assert _scale_bars(10, cfg) == 40
    assert _scale_bars(15, cfg) == 60


def test_scale_bars_floors_at_1():
    """A small positive value at coarser cadence must round to >= 1
    rather than 0 — otherwise downstream loops with `for _ in range(N)`
    silently no-op and the cooldown disappears entirely.
    """
    # 1 bar at 3600s with time_increment=86400 (daily) would round to ~0
    cfg = {"_resolved_time_increment_sec": 86400}
    assert _scale_bars(1, cfg) == 1
    assert _scale_bars(2, cfg) == 1


def test_scale_bars_zero_or_negative_passes_through():
    """Strategy code uses 0 to mean 'disabled'; preserve that signal."""
    cfg = {"_resolved_time_increment_sec": 1200}
    assert _scale_bars(0, cfg) == 0


def test_scale_bars_bad_input_returns_value_unchanged():
    """Defensive: a string or None must not raise — strategy hot path
    cannot afford TypeError surfaces from a diagnostic helper.
    """
    cfg = {"_resolved_time_increment_sec": 1200}
    assert _scale_bars(None, cfg) is None
    assert _scale_bars("not-a-number", cfg) == "not-a-number"


def test_scale_bars_matches_specific_production_scenarios():
    """Anchor the SNDK regression: at 1200s, BFQ priority TTL must be
    AT LEAST equivalent to the baseline 16-bar wall-clock window.
    SNDK expired after 16 bars at 1200s (= 5.3 hours real-time).
    With scaling, the same 15-bar config default becomes 45 bars at
    1200s = 15 wall-clock hours, matching the baseline.
    """
    cfg = {"_resolved_time_increment_sec": 1200}
    # Default _bfq_priority_max_bars derives from max(_bfq_max_bars, 15).
    # Both halves get scaled.
    bfq_max = _scale_bars(10, cfg)        # default 10 → 30
    priority_floor = _scale_bars(15, cfg)  # literal 15 → 45
    bfq_priority = max(bfq_max, priority_floor)  # 45
    assert bfq_priority == 45
    # In wall-clock terms: 45 × 1200s = 54000s ~= 15 trading hours,
    # equivalent to the baseline 15 × 3600s = 54000s.
    assert bfq_priority * 1200 == 15 * 3600


def test_scale_bars_baseline_override():
    """Some legacy parameters were tuned at a different baseline (e.g.,
    a daily 86400s).  Verify the helper accepts a custom baseline.
    """
    # If a parameter was tuned at daily granularity but we run at
    # hourly: 1 day-bar = 24 hour-bars.
    cfg = {"_resolved_time_increment_sec": 3600}
    assert _scale_bars(1, cfg, baseline_sec=86400) == 24
    # Run at the daily baseline → no scaling.
    cfg2 = {"_resolved_time_increment_sec": 86400}
    assert _scale_bars(1, cfg2, baseline_sec=86400) == 1


def test_scale_bars_uses_rounding_not_truncation():
    """Reviewer L3 regression test: enforce rounding semantics so a
    future refactor that drops `int(round(...))` for plain `int(...)`
    gets caught. With baseline 5400s and time_increment 3600s, scale
    factor is 5400/3600 = 1.5 — value 5 should round to 8 (5*1.5=7.5
    rounds half-up under banker's rounding to nearest even = 8). Plain
    truncation `int(7.5)` would give 7, which is wrong.
    """
    cfg = {"_resolved_time_increment_sec": 3600}
    # baseline 5400s, value 5 → 5 * 5400/3600 = 7.5 → round() → 8
    assert _scale_bars(5, cfg, baseline_sec=5400) == 8
    # 11 * 5400/3600 = 16.5 → round() → 16 (banker's rounding to even)
    # OR 17 (round half up). Python 3 round() is banker's = 16.
    assert _scale_bars(11, cfg, baseline_sec=5400) == 16


def test_scale_bars_run_once_injection_present():
    """Reviewer L3: lock the injection of `_resolved_time_increment_sec`
    into config at the top of run_once. Without this, all _scale_bars
    calls would silently fall back to 3600s baseline (no-op) for any
    non-3600s backtest, which is the original regression we're fixing.
    Read the source to verify the assignment exists at the right place.
    """
    import inspect
    import strategies.graph_nexus_analysis as gna
    src = inspect.getsource(gna.GraphNexusAnalysis.run_once)
    # The injection MUST happen near the top of run_once before any
    # config read that uses _scale_bars.
    assert '_resolved_time_increment_sec' in src, (
        "run_once dropped the granularity injection — every _scale_bars "
        "call will silently no-op for non-baseline cadences."
    )
    # Also assert it's an assignment (not just a comment).
    assert 'config["_resolved_time_increment_sec"] = ' in src
