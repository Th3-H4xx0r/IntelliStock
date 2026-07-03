"""Task 12 — rotation positive-graph gate + single_position_max_pct cap.

Trade-economics forensics on backtest 586767 found 27/28 exits were MECHANICAL
rotations ejecting positions the graph still rated positive (AMAT rotated at
+9.3% with raw=+0.284 HOLD, then ran +30% in 12 more days). Historical +266%
runs held winners 14-28 days. Two fixes are locked here:

  1. ROTATION GRAPH GATE — when ``rotation_positive_graph_gate_enabled`` is on,
     a held position whose CURRENT raw graph signal is positive can never be
     selected as a rotation-funding sell, regardless of any winner-lock-bypass /
     break-glass / gamma threshold. Config-gated (default OFF) so existing
     rotation behavior is unchanged unless the operator opts in.

  2. SINGLE POSITION CAP — the previously-DEAD config key
     ``single_position_max_pct`` (doc-179 = 25) is wired as a REAL cap at the
     three add paths: initial buy sizing, winner-adds, momentum amplifier. Any
     order that would push position_value / portfolio_total above pct/100 is
     clipped to the cap (or skipped if already at/above it). Absent key ⇒ no
     behavior change (today's uncapped default preserved).
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.strategies.graph_nexus_analysis as gna
from backend.tests.nexus_real_config import real_config


# ──────────────────────────────────────────────────────────────────────────
# Fix 1 — rotation positive-graph gate
# ──────────────────────────────────────────────────────────────────────────

# Shared AMAT-like held snapshot: profitable, inside the winner-lock window, so
# an incoming raw=1.8 candidate meets the gamma winner-lock-bypass thresholds.
def _amat_like(**overrides):
    base = dict(
        held_pnl_pct=9.3,
        held_rotation_score=0.0,
        held_days=9,
        held_raw_score=0.284,   # graph still rates it a POSITIVE HOLD
        drop_from_peak_pct=3.0,  # within the 8% winner-lock window
        is_equity=True,
        incoming_raw_score=1.8,  # meets rotation_winner_lock_bypass_min_raw_score
        incoming_rotation_score=1.8,
        held_sym="AMAT",
        market_regime="bull",
    )
    base.update(overrides)
    return base


def test_positive_graph_held_not_rotated_when_gate_enabled():
    """(a) raw=+0.284 winner with all bypass thresholds met → NOT selected."""
    allowed, _delta, mode = gna._rotation_candidate_allowed(
        config=real_config(rotation_positive_graph_gate_enabled=True),
        **_amat_like(),
    )
    assert allowed is False
    assert mode == "positive_graph_gate"


def test_positive_graph_held_IS_rotated_when_gate_disabled():
    """Control: with the gate OFF (default), the same position rotates via the
    gamma winner-lock bypass — proving the gate is the sole blocker."""
    allowed, _delta, mode = gna._rotation_candidate_allowed(
        config=real_config(),  # key absent ⇒ gate off
        **_amat_like(),
    )
    assert allowed is True
    assert mode == "gamma_winner_lock_bypass"


def test_negative_graph_winner_still_rotates_with_gate_enabled():
    """(b) raw=−0.5 (same +pnl) → rotation still works (graph is negative)."""
    allowed, _delta, mode = gna._rotation_candidate_allowed(
        config=real_config(rotation_positive_graph_gate_enabled=True),
        held_pnl_pct=9.3,
        held_rotation_score=0.0,
        held_days=25,            # past profitable full-exit hold window (20)
        held_raw_score=-0.5,     # graph rates it a SELL
        drop_from_peak_pct=10.0,  # >= 8% profitable-hold peak-drop gate
        is_equity=True,
        incoming_raw_score=2.5,   # >= profitable_min_incoming_raw_score (2.0)
        incoming_rotation_score=2.0,  # delta 2.0 >= profitable_min_delta (1.5)
        held_sym="OLD",
        market_regime="bull",
    )
    assert allowed is True
    assert mode == "profitable_hold"


def test_negative_graph_loser_still_rotates_with_gate_enabled():
    """A losing hold with negative graph signal still rotates out."""
    allowed, _delta, mode = gna._rotation_candidate_allowed(
        config=real_config(rotation_positive_graph_gate_enabled=True),
        held_pnl_pct=-8.0,       # <= rotation_replace_loss_threshold_pct (-5)
        held_rotation_score=0.0,
        held_days=35,            # >= rotation_min_hold_days (doc-179 = 30)
        held_raw_score=-0.5,
        drop_from_peak_pct=None,
        is_equity=True,
        incoming_raw_score=2.5,
        incoming_rotation_score=2.0,
        held_sym="LOSER",
        market_regime="bull",
    )
    assert allowed is True
    assert mode == "losing_hold"


# ──────────────────────────────────────────────────────────────────────────
# Fix 2 — single_position_max_pct cap
# ──────────────────────────────────────────────────────────────────────────


def test_cap_helper_clips_to_cap():
    """Helper clips an order that would breach the cap down to the headroom."""
    # $6k portfolio, new position, intended $1800 (30%), cap 25% ⇒ $1500.
    clipped = gna._clip_to_single_position_cap(
        sym="NEW",
        intended_value=1800.0,
        current_position_value=0.0,
        portfolio_total=6000.0,
        config={"single_position_max_pct": 25},
    )
    assert round(clipped, 2) == 1500.0


def test_cap_helper_skips_when_already_at_cap():
    """Already at/above the cap ⇒ order fully skipped (returns 0)."""
    clipped = gna._clip_to_single_position_cap(
        sym="FULL",
        intended_value=400.0,
        current_position_value=2600.0,  # 26% of 10k, already over 25%
        portfolio_total=10000.0,
        config={"single_position_max_pct": 25},
    )
    assert clipped == 0.0


def test_cap_helper_noop_when_key_absent():
    """Key absent ⇒ uncapped (today's default) — order passes unchanged."""
    clipped = gna._clip_to_single_position_cap(
        sym="NEW",
        intended_value=1800.0,
        current_position_value=0.0,
        portfolio_total=6000.0,
        config={},  # no single_position_max_pct
    )
    assert clipped == 1800.0


def test_initial_buy_clipped_to_cap():
    """(c) A buy that would create a 30% position on a $6k portfolio → 25%."""
    cand = {"ticker": "NEW", "raw_net_score": 0.60}
    funded, _queued, _meta = gna._plan_executable_stock_buy_slate(
        [cand],
        1800.0,  # 30% of 6000
        min_position_size=100.0,
        config=real_config(),  # single_position_max_pct = 25 (doc-179)
        portfolio_total=6000.0,
    )
    assert len(funded) == 1
    assert round(float(funded[0]["buy_cash"]), 2) == 1500.0


def test_initial_buy_uncapped_when_key_absent():
    """Absent key ⇒ no behavior change: the full $1800 is funded."""
    cand = {"ticker": "NEW", "raw_net_score": 0.60}
    funded, _queued, _meta = gna._plan_executable_stock_buy_slate(
        [cand],
        1800.0,
        min_position_size=100.0,
        config=real_config(single_position_max_pct=None),  # treated as absent
        portfolio_total=6000.0,
    )
    assert len(funded) == 1
    assert round(float(funded[0]["buy_cash"]), 2) == 1800.0


def test_winner_add_clipped_to_cap():
    """(d) A winner-add pushing 24%→28% of a $10k portfolio → clipped to 25%."""
    item = {
        "ticker": "WIN",
        "raw_net_score": 0.60,
        "held_days": 10,
        "unrealized_pct": 200.0,     # pnl gate (>= 8%)
        "drop_from_peak_pct": 0.0,
        "existing_add_count": 0,
        "entry_notional": 800.0,     # add = 800 * 0.5 = 400 (would push +4% → 28%)
        "position_value": 2400.0,    # currently 24% of the portfolio
        "is_equity": True,
    }
    funded, _remaining = gna._plan_winner_adds(
        [item],
        10000.0,
        min_position_size=50.0,
        config=real_config(),
        portfolio_total=10000.0,
    )
    assert len(funded) == 1
    # headroom to the 25% cap = 2500 - 2400 = 100
    assert round(float(funded[0]["buy_cash"]), 2) == 100.0


def test_winner_add_skipped_when_already_over_cap():
    """A winner-add on a position already over the cap is skipped entirely."""
    item = {
        "ticker": "WIN",
        "raw_net_score": 0.60,
        "held_days": 10,
        "unrealized_pct": 200.0,
        "drop_from_peak_pct": 0.0,
        "existing_add_count": 0,
        "entry_notional": 800.0,
        "position_value": 2600.0,    # 26% — already over the 25% cap
        "is_equity": True,
    }
    funded, _remaining = gna._plan_winner_adds(
        [item],
        10000.0,
        min_position_size=50.0,
        config=real_config(),
        portfolio_total=10000.0,
    )
    assert funded == []
