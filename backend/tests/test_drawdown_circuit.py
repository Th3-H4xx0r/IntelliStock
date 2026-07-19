"""Tiered drawdown circuit (2026-07-19 regime-safety spec, Phase 4).

soft(-5%, SPY-corroborated) halts buys; hard(-9%) also tightens the cut
floor; kill(-12%) liquidates. Classifier-independent: driven purely by NAV
drawdown from the rolling peak.
"""
import os
import sys
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


class _Emu:
    def __init__(self, value, positions=None):
        self._value = value
        self._initial_value = 6000.0
        self._pos = positions or {}

    def get_portfolio_value(self, prices=None):
        return self._value

    def get_positions(self):
        return dict(self._pos)

    def get_cash(self):
        return 0.0


def _spy_bars(ret20_pct: float):
    """60 daily SPY closes whose last-vs-21st-back return is ret20_pct."""
    base = datetime(2026, 1, 2)
    closes = [100.0] * 40 + [100.0 * (1 + ret20_pct / 100.0)] * 20
    return [
        {"t": (base + timedelta(days=i)).strftime("%Y-%m-%dT05:00:00Z"), "c": c}
        for i, c in enumerate(closes)
    ]


def _run(value, spy_ret20=None, positions=None, cfg=None, cache=None):
    cache = cache if cache is not None else {}
    cache.setdefault("_portfolio_drawdown_state", {"peak_value": 6000.0})
    if spy_ret20 is not None:
        cache.setdefault("_overlay_bars_raw", {"SPY": _spy_bars(spy_ret20)})
    scores = {"NEW": {"score": 1, "action_intent": "initial_buy", "reason": "x"}}
    for sym in (positions or {}):
        scores.setdefault(sym, {"score": 0, "action_intent": "hold"})
    out = g._apply_portfolio_drawdown_halt(
        scores, list(scores.keys()), _Emu(value, positions), cfg or {},
        cache, {}, {}, "2026-03-10",
    )
    return out, cache


def test_no_tier_above_soft_threshold():
    out, cache = _run(5800.0, spy_ret20=-2.0)  # -3.3% dd
    assert out["NEW"]["score"] == 1
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == ""


def test_soft_tier_blocks_buys_when_spy_corroborates():
    out, cache = _run(5650.0, spy_ret20=-2.0)  # -5.8% dd, SPY down
    assert out["NEW"]["score"] == 0
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "soft"


def test_soft_tier_suppressed_in_rising_market():
    out, cache = _run(5650.0, spy_ret20=+3.0)  # -5.8% dd, SPY up → keep buying
    assert out["NEW"]["score"] == 1
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == ""


def test_soft_tier_blind_spy_is_conservative():
    out, cache = _run(5650.0, spy_ret20=None)  # no SPY data → block
    assert out["NEW"]["score"] == 0
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "soft"


def test_hard_tier_sets_cut_floor_override():
    out, cache = _run(5430.0, spy_ret20=+3.0)  # -9.5% dd — corroboration irrelevant
    assert out["NEW"]["score"] == 0
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "hard"
    assert cache["_dd_cut_floor_override"] == -7.0


def test_kill_tier_liquidates_held():
    out, cache = _run(5200.0, spy_ret20=-4.0,  # -13.3% dd
                      positions={"AAA": 1.0, "BBB": 2.0})
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == "kill"
    assert out["AAA"]["score"] == -1 and out["BBB"]["score"] == -1
    assert "Circuit breaker" in out["AAA"]["reason"]  # survives grace gates
    assert out["NEW"]["score"] == 0


def test_circuit_disabled_by_config():
    out, cache = _run(5200.0, spy_ret20=-4.0, cfg={"drawdown_circuit_enabled": False})
    assert cache["_portfolio_drawdown_state"]["circuit_tier"] == ""
    assert out["NEW"]["score"] == 1  # legacy 15% halt not reached either


def test_override_cleared_when_recovered():
    _, cache = _run(5430.0, spy_ret20=+3.0)  # hard → override set
    assert "_dd_cut_floor_override" in cache
    out2, cache2 = _run(6000.0, spy_ret20=+3.0, cache=cache)  # recovered
    assert "_dd_cut_floor_override" not in cache2
