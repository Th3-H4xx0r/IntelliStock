"""Volatility targeting on the levered core.

Twelve protective variants have been measured and rejected (SQQQ at four gate
settings, three defensive chop occupants, four de-lever stops, dip re-entry) and
every one failed the SAME way: they are BINARY. Fully leaving the levered core
means missing the recovery, so the bull cost always exceeded the bear saving.

Vol targeting is continuous — hold less of the same position when it is more
dangerous, rather than none of it. Two reasons to expect a different result:
Moreira & Muir's volatility-managed portfolios, and this repo's own crypto
MeanRev vol-scaled sizing, which "wins/ties equal-weight in ALL 7 regimes".

The scale multiplies the LEVERED leg only; the freed weight goes to the
unlevered chop occupant, exactly as an unfilled sleeve does. Off by default
(`core_vol_target = 0`) until measured.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_x import core_vol_scale, plan_targets  # noqa: E402


def _closes(vol_daily, n=80, start=100.0):
    """A deterministic zig-zag with a known per-bar move, so realised vol is
    predictable without random numbers."""
    out, p = [start], start
    for i in range(n - 1):
        p = p * (1 + vol_daily if i % 2 == 0 else 1 - vol_daily)
        out.append(p)
    return out


CFG = {"core_bull_symbol": "TQQQ", "core_chop_symbol": "SPY",
       "core_bear_symbol": "", "core_weight": 1.0,
       "satellite_pct": 0.0, "commodity_pct": 0.0,
       "core_vol_bars": 20, "core_leverage_factor": 3.0,
       "core_vol_scale_min": 0.3, "core_vol_scale_max": 1.0}


def test_off_by_default_returns_full_scale():
    assert core_vol_scale(_closes(0.01), dict(CFG)) == 1.0


def test_calm_markets_keep_full_leverage():
    """The scale is capped at 1.0 — this never ADDS leverage beyond design."""
    cfg = dict(CFG, core_vol_target=0.25)
    assert core_vol_scale(_closes(0.002), cfg) == 1.0


def test_violent_markets_cut_the_levered_weight():
    cfg = dict(CFG, core_vol_target=0.25)
    calm = core_vol_scale(_closes(0.004), cfg)
    wild = core_vol_scale(_closes(0.030), cfg)
    assert wild < calm, f"vol scale did not fall as vol rose: {wild} vs {calm}"
    assert wild >= cfg["core_vol_scale_min"] - 1e-9


def test_the_scale_is_clamped_at_both_ends():
    cfg = dict(CFG, core_vol_target=0.25)
    assert core_vol_scale(_closes(0.20), cfg) >= 0.3
    assert core_vol_scale(_closes(0.0001), cfg) <= 1.0


def test_too_few_closes_is_full_scale_not_zero():
    """A cold start must not silently de-lever to the floor."""
    cfg = dict(CFG, core_vol_target=0.25)
    assert core_vol_scale([100.0, 101.0], cfg) == 1.0


def test_plan_targets_routes_the_freed_weight_to_the_unlevered_occupant():
    cfg = dict(CFG, core_vol_target=0.25)
    t, notes = plan_targets(risk_on=True, config=cfg, vol_scale=0.5)
    assert t["TQQQ"] == 0.5, f"levered leg not scaled: {t}"
    assert t["SPY"] == 0.5, f"freed weight did not go to the chop occupant: {t}"
    assert abs(sum(t.values()) - 1.0) < 1e-9
    assert any("vol" in n.lower() for n in notes)


def test_a_scale_of_one_is_byte_identical_to_today():
    a, _ = plan_targets(risk_on=True, config=dict(CFG))
    b, _ = plan_targets(risk_on=True, config=dict(CFG), vol_scale=1.0)
    assert a == b


def test_risk_off_is_untouched_there_is_no_levered_leg_to_scale():
    t, _ = plan_targets(risk_on=False, config=dict(CFG), vol_scale=0.4)
    assert t.get("SPY") == 1.0
    assert "TQQQ" not in t
