"""Backfill-queue buy lane must honour the regime position cap (2026-07-25).

THE BUG: the queue's headroom is computed from the RAW `max_positions`:

    _bfq_headroom = max(0, int(config.get("max_positions", 15) or 15)
                        - _bfq_positions - _planned_new_positions)

Every other entry path derives its cap from the regime (`_regime_position_cap`,
then `_apply_recovery_cap`). So in a confirmed bear with max_positions_bear=2
the queue still believed it had room for 14 positions and kept opening fresh
longs into the downtrend. This is the same class of defect `_rotation_lane_allowed`
was written for -- its docstring notes those lanes "historically BYPASSED the
position cap" -- but the queue BUY path never got the corresponding guard (only
the queue ROTATION path calls `_rotation_lane_allowed`).

Observed on bt#611163's bear leg: 20 `backfill_queue_buy` + 61 `queued_backfill`
against only 15 `initial_buy`, while the bear-tuned reference (bt#418917) kept
its long book to -$175 and finished +6.88%.

`backfill_queue_regime_cap_enabled` (default OFF) makes the queue use the same
regime-adjusted cap as the Z4.1 gate, including the recovery raise -- so a
confirmed recovery still lifts it to max_positions_recovery rather than pinning
it at the bear cap.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g

_CFG = {
    "max_positions": 14,
    "max_positions_bull": 14,
    "max_positions_chop": 8,
    "max_positions_bear": 2,
    "max_positions_crash": 0,
}


def test_regime_cap_helper_values():
    assert g._regime_position_cap(_CFG, "bull") == 14
    assert g._regime_position_cap(_CFG, "chop") == 8
    assert g._regime_position_cap(_CFG, "bear") == 2
    assert g._regime_position_cap(_CFG, "crash") == 0


def test_recovery_cap_only_raises():
    cfg = dict(_CFG, max_positions_recovery=14)
    # flag off -> identity
    assert g._apply_recovery_cap(2, None, cfg) == 2
    assert g._apply_recovery_cap(2, False, cfg) == 2
    # flag on -> raised to the recovery cap
    assert g._apply_recovery_cap(2, True, cfg) == 14
    # never lowers
    assert g._apply_recovery_cap(14, True, dict(_CFG, max_positions_recovery=8)) == 14
    # key absent -> identity
    assert g._apply_recovery_cap(2, True, _CFG) == 2


def _headroom(regime, held, cfg, recovery=False, planned=0):
    """Mirror of the queue's headroom computation, via the shared helper."""
    return g._bfq_regime_headroom(cfg, regime, recovery, held, planned)


def test_headroom_defaults_to_raw_max_positions_when_disabled():
    """Default OFF reproduces today's behavior byte-identically."""
    cfg = dict(_CFG)  # flag absent
    assert _headroom("bear", 2, cfg) == 12   # 14 - 2, the buggy-but-current value


def test_headroom_respects_bear_cap_when_enabled():
    """THE FIX: in a confirmed bear the queue gets no headroom past the bear cap."""
    cfg = dict(_CFG, backfill_queue_regime_cap_enabled=True)
    assert _headroom("bear", 2, cfg) == 0
    assert _headroom("bear", 0, cfg) == 2
    assert _headroom("crash", 0, cfg) == 0


def test_headroom_unchanged_in_bull_when_enabled():
    """The gate must not throttle the bull leg -- bull cap == max_positions."""
    cfg = dict(_CFG, backfill_queue_regime_cap_enabled=True)
    assert _headroom("bull", 3, cfg) == 11
    assert _headroom("chop", 3, cfg) == 5


def test_headroom_recovery_raises_cap_when_enabled():
    """A confirmed recovery must not be pinned at the bear cap."""
    cfg = dict(_CFG, backfill_queue_regime_cap_enabled=True, max_positions_recovery=14)
    assert _headroom("chop", 3, cfg, recovery=True) == 11
    assert _headroom("bear", 3, cfg, recovery=True) == 11


def test_headroom_never_negative_and_counts_planned():
    cfg = dict(_CFG, backfill_queue_regime_cap_enabled=True)
    assert _headroom("bear", 5, cfg) == 0
    assert _headroom("bull", 3, cfg, planned=4) == 7
