"""Bear relative-strength entry gate (2026-07-19): in bear/crash, only names
with non-negative recent returns may take the few allowed slots."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def _hist(closes):
    return [{"close": c} for c in closes]


def _run(regime, closes, cfg=None, score=1):
    scores = {"XYZ": {"score": score, "action_intent": "initial_buy",
                      "reason": "r", "raw_net_score": 0.5}}
    cache = {"_market_regime": regime}
    out = g._apply_quality_filter(
        scores, ["XYZ"], {"XYZ": closes[-1] if closes else 100.0},
        {"XYZ": _hist(closes)}, None,
        dict({"quality_filter_missing_metadata_policy": "warn"}, **(cfg or {})),
        strategy_cache=cache,
    )
    return out["XYZ"]


def test_recent_return_pct():
    assert abs(g._recent_return_pct("A", {"A": _hist([100, 110])}, 20) - 10.0) < 1e-9
    assert g._recent_return_pct("A", {"A": _hist([100])}, 20) is None
    assert g._recent_return_pct("A", {}, 20) is None


def test_bear_blocks_falling_name():
    sc = _run("bear", [100.0] * 10 + [90.0] * 10)  # -10% over lookback
    assert sc["score"] == 0
    assert "Bear RS gate" in sc["reason"]


def test_bear_allows_rising_name():
    sc = _run("bear", [100.0] * 10 + [104.0] * 10)  # +4%
    assert sc["score"] == 1


def test_bear_blocks_unknown_history():
    sc = _run("bear", [100.0])  # too short → unknown → block in bear
    assert sc["score"] == 0


def test_bull_and_chop_unaffected():
    for regime in ("bull", "chop", ""):
        sc = _run(regime, [100.0] * 10 + [90.0] * 10)
        assert sc["score"] == 1, f"regime={regime!r} must not RS-block"


def test_gate_config_off():
    sc = _run("bear", [100.0] * 10 + [90.0] * 10,
              cfg={"bear_entry_rs_filter_enabled": False})
    assert sc["score"] == 1


def test_min_return_configurable():
    sc = _run("bear", [100.0] * 10 + [104.0] * 10,
              cfg={"bear_entry_rs_min_return_pct": 5.0})
    assert sc["score"] == 0, "+4% must fail a +5% RS floor"


def test_momentum_lane_extension_block_no_bypass():
    # BULL_F7e: CAR raw=5.56 bypassed the ATH gate's conviction escape and
    # entered parabolic. The extension gate has NO bypass.
    hist = {"CAR": [{"close": 450.0}] * 5 + [{"close": 665.0}] * 15}  # +48% runup
    blocked, why = g._v32_momentum_ath_or_mcap_block(
        "CAR", 665.0, hist, {}, {"entry_extension_block_pct": 25.0}, 5.56,
        lane="mw_swap")
    assert blocked and "extension" in why


def test_momentum_lane_extension_allows_calm_names():
    hist = {"CVX": [{"close": 185.0}] * 10 + [{"close": 190.0}] * 10}  # +2.7%
    blocked, _ = g._v32_momentum_ath_or_mcap_block(
        "CVX", 190.0, hist, {}, {"entry_extension_block_pct": 25.0,
                                 "portfolio_swap_ath_gate_enabled": False}, 1.0,
        lane="mw_swap")
    assert not blocked
