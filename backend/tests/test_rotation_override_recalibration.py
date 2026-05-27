"""Rotation override recalibration (kimi-k2.5 fix).

Spec: docs/superpowers/specs/2026-05-26-rotation-override-recalibration-kimi-design.md

Covers: (a) the new flag-gated profitable_min_hold conviction override, and
(b) the recalibrated break_glass / profitable_hold floors firing at the 1.8
raw-score ceiling. _rotation_candidate_allowed returns (allow, delta, reason).
"""
from backend.strategies.graph_nexus_analysis import _rotation_candidate_allowed


def _cfg(**overrides):
    base = {
        "rotation_min_delta": 0.15,
        "rotation_min_hold_days": 10,
        "rotation_profitable_min_delta": 1.0,
        "rotation_profitable_full_exit_min_hold_days": 20,
        "rotation_profitable_min_incoming_raw_score": 1.5,
        "rotation_profitable_hold_min_peak_drop_pct": 8.0,
        "rotation_winner_lock_enabled": True,
        "rotation_winner_lock_min_hold_days": 3,
        "rotation_winner_lock_min_pnl_pct": 2.0,
        "rotation_winner_lock_min_raw_score": -0.10,
        "rotation_winner_lock_max_peak_drawdown_pct": 8.0,
        "rotation_break_glass_raw_score": 1.5,
        "rotation_break_glass_delta": 1.0,
        "rotation_min_score": 0.40,
    }
    base.update(overrides)
    return base


def test_profitable_min_hold_conviction_override_fires_when_enabled():
    # Incumbent: +7% pnl, held 8d (< 20d profitable_full_exit), raw negative so
    # NOT winner-locked -> falls into the profitable_min_hold branch.
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,  # delta = 1.3
        config=_cfg(
            profitable_min_hold_conviction_override_enabled=True,
            profitable_min_hold_conviction_min_raw_score=1.5,
            profitable_min_hold_conviction_min_delta=1.0,
            profitable_min_hold_conviction_max_held_pnl_pct=10.0,
        ),
        market_regime="bull",
    )
    assert allow is True
    assert reason == "profitable_min_hold_conviction_override"


def test_profitable_min_hold_blocks_when_override_disabled_default():
    # Same inputs, flag absent (default) -> preserves current behavior.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_cfg(),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_genuine_winner_protected_from_conviction_override():
    # Incumbent +15% pnl (>= max_held_pnl 10) -> override must NOT fire.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=15.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_cfg(
            profitable_min_hold_conviction_override_enabled=True,
            profitable_min_hold_conviction_min_raw_score=1.5,
            profitable_min_hold_conviction_min_delta=1.0,
            profitable_min_hold_conviction_max_held_pnl_pct=10.0,
        ),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_subthreshold_challenger_does_not_trigger_override():
    # incoming raw 1.4 < 1.5 floor -> no override.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.40,
        incoming_rotation_score=1.3,
        config=_cfg(
            profitable_min_hold_conviction_override_enabled=True,
            profitable_min_hold_conviction_min_raw_score=1.5,
            profitable_min_hold_conviction_min_delta=1.0,
            profitable_min_hold_conviction_max_held_pnl_pct=10.0,
        ),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_break_glass_revived_at_ceiling():
    # Winner-locked incumbent (+5% pnl, 6d, raw positive, no drawdown).
    # Challenger raw 1.6 >= new break_glass 1.5, delta 1.1 >= 1.0 -> pierces lock.
    # With the old 3.50 floor this would return "winner_lock".
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=5.0,
        held_rotation_score=0.0,
        held_days=6,
        held_raw_score=0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.6,
        incoming_rotation_score=1.1,  # delta = 1.1
        config=_cfg(),
        market_regime="bull",
    )
    assert allow is True
    assert reason in ("break_glass_trim", "gamma_winner_lock_bypass")


def test_profitable_hold_gate_revived_at_ceiling():
    # Held 22d (>= profitable_full_exit 20), +6% pnl, 10% off peak (>= 8 gate),
    # raw negative so not winner-locked. Challenger raw 1.6 >= new 1.5
    # profitable_min_incoming floor, delta 1.1 >= profitable_min_delta 1.0.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=6.0,
        held_rotation_score=0.0,
        held_days=22,
        held_raw_score=-0.5,
        drop_from_peak_pct=10.0,
        is_equity=True,
        incoming_raw_score=1.6,
        incoming_rotation_score=1.1,
        config=_cfg(),
        market_regime="bull",
    )
    assert allow is True
    assert reason == "profitable_hold"
