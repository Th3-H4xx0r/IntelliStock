"""Tier-3 Phase 3 — V31.4 cooldown lift threshold + conviction telemetry.

Spec: docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md (A5, telemetry)
"""
from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402


# ── A5 + telemetry: surfaced knobs ──────────────────────────────────────────


def test_effective_nexus_config_surfaces_phase3_knobs():
    cfg = gna._get_effective_nexus_config({})
    assert "post_sell_cooldown_lift_threshold_pct" in cfg
    assert "conviction_telemetry_enabled" in cfg


def test_post_sell_cooldown_lift_default():
    """A5 default 10% recovery shortcut threshold."""
    cfg = gna._get_effective_nexus_config({})
    assert cfg["post_sell_cooldown_lift_threshold_pct"] == 0.10


def test_conviction_telemetry_default_enabled():
    cfg = gna._get_effective_nexus_config({})
    assert cfg["conviction_telemetry_enabled"] is True


# ── Conviction-score helper ─────────────────────────────────────────────────


def test_conviction_score_zero_for_unknown_symbol():
    score = gna._compute_conviction_score("", config={}, strategy_cache={}, propagated={})
    assert score == 0.0


def test_conviction_score_mcap_only():
    """Mega-cap with no propagated data still scores positive from mcap component."""
    cache = {"_yf_market_cap_cache": {"AAPL": 3_000_000_000_000}}
    score = gna._compute_conviction_score("AAPL", config={}, strategy_cache=cache, propagated={})
    assert score > 0.0
    assert score <= 1.0


def test_conviction_score_raw_only():
    """Raw_score-only contribution scales linearly up to 4.0 cap."""
    propagated = {"FOO": {"raw_score": 4.0}}
    score = gna._compute_conviction_score("FOO", config={}, strategy_cache={}, propagated=propagated)
    assert 0.30 <= score <= 0.40  # ~0.35 from raw component


def test_conviction_score_combines_components():
    """Mega-cap + strong score + positive sentiment + catalyst ~= near 1.0."""
    cache = {"_yf_market_cap_cache": {"NVDA": 2_000_000_000_000}}
    propagated = {"NVDA": {"raw_score": 3.0, "sentiment": 1.0, "is_catalyst": True}}
    score = gna._compute_conviction_score("NVDA", config={}, strategy_cache=cache, propagated=propagated)
    # Lower bound: components should sum to >= 0.85 (close to max)
    assert score >= 0.85


def test_conviction_score_never_exceeds_1():
    """Hard cap at 1.0 regardless of inputs."""
    cache = {"_yf_market_cap_cache": {"X": 10_000_000_000_000}}  # absurd mcap
    propagated = {"X": {"raw_score": 100.0, "sentiment": 10.0, "is_catalyst": True}}
    score = gna._compute_conviction_score("X", config={}, strategy_cache=cache, propagated=propagated)
    assert 0.0 <= score <= 1.0


def test_conviction_score_handles_bad_inputs():
    """Returns 0.0 on malformed inputs, never raises."""
    score = gna._compute_conviction_score(
        "AAA",
        config={},
        strategy_cache={"_yf_market_cap_cache": {"AAA": "bad"}},
        propagated={"AAA": {"raw_score": None}},
    )
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


# ── A5 custom threshold ────────────────────────────────────────────────────


def test_post_sell_cooldown_lift_threshold_override():
    """Operator can tune the recovery shortcut percentage."""
    cfg = gna._get_effective_nexus_config({"post_sell_cooldown_lift_threshold_pct": 0.20})
    assert cfg["post_sell_cooldown_lift_threshold_pct"] == 0.20
