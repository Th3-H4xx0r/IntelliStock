"""Rotation override recalibration (kimi-k2.5 fix).

Spec: docs/superpowers/specs/2026-05-26-rotation-override-recalibration-kimi-design.md

Covers: (a) the new flag-gated profitable_min_hold conviction override (including
its held_days floor that protects fresh winners), and (b) the recalibrated
break_glass / profitable_hold floors firing at the 1.8 raw-score ceiling.
_rotation_candidate_allowed returns (allow, delta, reason).

Note: _cfg sets rotation_min_hold_days=10, so in the default "bull" regime the
override's held_days floor (min_hold_days) is 10. Override-path tests use
held_days=12 (past the floor) so they isolate the intended gate; the floor
itself is exercised by test_fresh_hold_* and the boundary test.
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


def _override_cfg(**extra):
    return _cfg(
        profitable_min_hold_conviction_override_enabled=True,
        profitable_min_hold_conviction_min_raw_score=1.5,
        profitable_min_hold_conviction_min_delta=1.0,
        profitable_min_hold_conviction_max_held_pnl_pct=10.0,
        **extra,
    )


def test_profitable_min_hold_conviction_override_fires_when_enabled():
    # Incumbent: +7% pnl, held 12d (>= 10 floor, < 20 profitable_full_exit), raw
    # negative so NOT winner-locked -> falls into the profitable_min_hold branch.
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=12,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,  # delta = 1.3
        config=_override_cfg(),
        market_regime="bull",
    )
    assert allow is True
    assert reason == "profitable_min_hold_conviction_override"


def test_profitable_min_hold_blocks_when_override_disabled_default():
    # Same inputs, flag absent (default) -> preserves current behavior.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=12,
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


def test_fresh_hold_not_churned_by_override():
    # held 5d < 10 floor: a fresh winner must NOT be churned even with the flag
    # on and a max-conviction challenger (protects V32 Phase 3 B-5 behavior).
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=5,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_override_cfg(),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_held_days_floor_boundary_fires_at_min_hold_days():
    # held exactly == min_hold_days (10): the >= floor allows the override.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=10,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_override_cfg(),
        market_regime="bull",
    )
    assert allow is True
    assert reason == "profitable_min_hold_conviction_override"


def test_genuine_winner_protected_from_conviction_override():
    # Incumbent +15% pnl (>= max_held_pnl 10), held 12d -> override must NOT fire
    # (isolates the pnl ceiling, since held_days is past the floor).
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=15.0,
        held_rotation_score=0.0,
        held_days=12,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_override_cfg(),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_pnl_ceiling_boundary_blocks_at_exactly_max():
    # held_pnl exactly 10.0 == max_held_pnl: the strict `< max_pnl` blocks it.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=10.0,
        held_rotation_score=0.0,
        held_days=12,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_override_cfg(),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_subthreshold_challenger_does_not_trigger_override():
    # incoming raw 1.4 < 1.5 floor, held 12d -> no override (isolates raw floor).
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=12,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.40,
        incoming_rotation_score=1.3,
        config=_override_cfg(),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_raw_and_delta_boundaries_fire_at_exact_thresholds():
    # incoming raw exactly 1.5, delta exactly 1.0 (>= thresholds) -> fires.
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=12,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.5,
        incoming_rotation_score=1.0,  # delta = 1.0
        config=_override_cfg(),
        market_regime="bull",
    )
    assert allow is True
    assert reason == "profitable_min_hold_conviction_override"


def test_break_glass_revived_at_ceiling():
    # Winner-locked incumbent (+5% pnl, 6d, raw positive, no drawdown).
    # Challenger raw 1.6 >= new break_glass 1.5, delta 1.1 >= 1.0 -> pierces lock.
    # gamma bypass needs raw >= 1.8 (default, not overridden) so 1.6 cannot use it;
    # only the recalibrated 1.5 break_glass floor lets this through. With the old
    # 3.50 floor it would return "winner_lock".
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
    assert reason == "break_glass_trim"


def test_profitable_hold_gate_revived_at_ceiling():
    # Held 22d (>= profitable_full_exit 20), +6% pnl, 10% off peak (>= 8 gate),
    # raw negative so not winner-locked. Challenger raw 1.6 >= new 1.5
    # profitable_min_incoming floor, delta 1.1 >= profitable_min_delta 1.0.
    # With the old 2.0 floor, incoming 1.6 < 2.0 -> would block.
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
