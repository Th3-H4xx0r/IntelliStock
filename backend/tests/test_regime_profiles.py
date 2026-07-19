"""Regime profiles: rotation-lane gating + per-regime caps (Phase 3)."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


class _Emu:
    def __init__(self, n):
        self._n = n

    def get_positions(self):
        return {f"S{i}": 1.0 for i in range(self._n)}


def test_regime_position_cap_table():
    cfg = {"max_positions": 14, "max_positions_bull": 14,
           "max_positions_chop": 8, "max_positions_bear": 8}
    assert g._regime_position_cap(cfg, "bull") == 14
    assert g._regime_position_cap(cfg, "chop") == 8
    assert g._regime_position_cap(cfg, "bear") == 8
    assert g._regime_position_cap(cfg, "crash") == 0


def test_lane_blocked_in_bear_and_crash():
    for regime in ("bear", "crash"):
        ok, why = g._rotation_lane_allowed({"_market_regime": regime}, _Emu(3), {})
        assert not ok and regime in why


def test_lane_allowed_in_bull():
    ok, _ = g._rotation_lane_allowed({"_market_regime": "bull"}, _Emu(15), {})
    assert ok


def test_lane_chop_respects_cap():
    cfg = {"max_positions": 14, "max_positions_chop": 8}
    ok, why = g._rotation_lane_allowed({"_market_regime": "chop"}, _Emu(9), cfg)
    assert not ok and "cap=8" in why
    ok, _ = g._rotation_lane_allowed({"_market_regime": "chop"}, _Emu(8), cfg)
    assert ok, "at-cap swap (1-for-1) stays allowed; only over-cap blocks"


def test_lane_gate_config_off():
    ok, _ = g._rotation_lane_allowed({"_market_regime": "bear"}, _Emu(3),
                                     {"rotation_lanes_regime_gated": False})
    assert ok


def test_lane_defaults_to_chop_when_regime_unset():
    cfg = {"max_positions": 14, "max_positions_chop": 8}
    ok, why = g._rotation_lane_allowed({}, _Emu(9), cfg)
    assert not ok and "chop" in why
