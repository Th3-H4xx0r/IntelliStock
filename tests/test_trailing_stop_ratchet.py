"""Run-185254 leak #4: ROBN went +24% -> -8.6%; the trailing stop only
evaluated while unrealized >= activation, so crashing through activation
disarmed the stop. Once armed, it must stay armed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.strategies.graph_nexus_analysis import (
    _resolve_position_peak_state,
    _trailing_stop_armed,
)


def test_arms_at_activation():
    sc = {}
    assert _trailing_stop_armed(sc, "_peak_AAPL_x", unrealized_pct=6.0,
                                activation=5.0, peak_protected=False) is True
    assert sc.get("_peak_AAPL_x::armed") is True


def test_stays_armed_below_activation():
    sc = {"_peak_AAPL_x::armed": True}
    assert _trailing_stop_armed(sc, "_peak_AAPL_x", unrealized_pct=-8.6,
                                activation=5.0, peak_protected=False) is True


def test_never_armed_stays_disarmed():
    assert _trailing_stop_armed({}, "_peak_AAPL_x", unrealized_pct=2.0,
                                activation=5.0, peak_protected=False) is False


def test_peak_protected_arms():
    sc = {}
    assert _trailing_stop_armed(sc, "_peak_AAPL_x", unrealized_pct=1.0,
                                activation=5.0, peak_protected=True) is True


def test_armed_flag_survives_peak_state_resolution():
    # _resolve_position_peak_state prunes stale _peak_{ticker}_* keys; the
    # armed flag for the CURRENT entry must survive that pruning.
    sc = {}
    peak_key, _ = _resolve_position_peak_state(sc, "ROBN", "2026-06-17", 28.74)
    assert _trailing_stop_armed(sc, peak_key, 24.0, 5.0, False) is True
    peak_key2, _ = _resolve_position_peak_state(sc, "ROBN", "2026-06-17", 35.70)
    assert peak_key2 == peak_key
    assert sc.get(f"{peak_key}::armed") is True  # not pruned
    # re-entry with a NEW entry key prunes the old armed flag
    _resolve_position_peak_state(sc, "ROBN", "2026-08-01", 30.00)
    assert f"{peak_key}::armed" not in sc
