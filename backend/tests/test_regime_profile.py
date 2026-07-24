"""Regime auto-switch: _apply_regime_profile overlay merge (2026-07-23).

DEFAULT-SAFE: a config with no `regime_profiles` key is returned unchanged
(byte-identical to today). With the key, the confirmed regime's overlay wins
over the base; unlisted keys stay at the base value.
"""
import ast
import os
import sys
import types

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# broker.py argparse-SystemExits under pytest — extract just the pure helper.
_WANTED = {"_apply_regime_profile"}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
_ns = {}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
apply_regime_profile = _ns["_apply_regime_profile"]

BULL = {"profit_take_gain_pct": 100, "trailing_stop_activation_pct": 40,
        "momentum_partial_trim_execution_enabled": True}
BASE = {
    "profit_take_gain_pct": 20,
    "trailing_stop_activation_pct": 15,
    "momentum_partial_trim_execution_enabled": False,
    "residual_sleeve_bear_hold_through_chop": True,   # bear-only lever
    "regime_upgrade_confirm_bars": 3,                 # transition lever (base)
    "regime_profiles": {"bull": BULL},
}


def test_default_safe_no_regime_profiles_is_identity():
    cfg = {"profit_take_gain_pct": 20, "trailing_stop_activation_pct": 15}
    out = apply_regime_profile(cfg, "bull")
    assert out is cfg, "no regime_profiles -> return the SAME object (byte-identical)"


def test_bull_regime_applies_bull_overlay():
    out = apply_regime_profile(BASE, "bull")
    assert out is not BASE, "must return a copy, not mutate the base"
    # overlay keys win
    assert out["profit_take_gain_pct"] == 100
    assert out["trailing_stop_activation_pct"] == 40
    assert out["momentum_partial_trim_execution_enabled"] is True
    # base-only keys survive (bear lever + transition lever untouched)
    assert out["residual_sleeve_bear_hold_through_chop"] is True
    assert out["regime_upgrade_confirm_bars"] == 3
    # the base object itself is NOT mutated
    assert BASE["profit_take_gain_pct"] == 20


def test_bear_and_chop_use_base_when_no_overlay():
    for regime in ("bear", "chop", "crash"):
        out = apply_regime_profile(BASE, regime)
        assert out is BASE, f"{regime}: no overlay -> base unchanged (identity)"
        assert out["profit_take_gain_pct"] == 20
        assert out["momentum_partial_trim_execution_enabled"] is False


def test_unknown_or_empty_regime_uses_base():
    for regime in ("", None, "sideways", "BULLISH"):
        out = apply_regime_profile(BASE, regime)
        assert out.get("profit_take_gain_pct") == 20, f"{regime!r} -> base"


def test_regime_case_insensitive():
    out = apply_regime_profile(BASE, "BULL")
    assert out["profit_take_gain_pct"] == 100, "regime match is case-insensitive"


def test_empty_profiles_dict_is_identity():
    cfg = {"x": 1, "regime_profiles": {}}
    assert apply_regime_profile(cfg, "bull") is cfg


def test_non_dict_config_returned_as_is():
    assert apply_regime_profile(None, "bull") is None
    assert apply_regime_profile("nope", "bull") == "nope"


def test_recovery_profile_overlay_applies():
    """v2 (2026-07-24): a distinct "recovery" overlay (used when the broker maps a
    recovery-confirmed chop to effective regime "recovery") applies its capture
    levers over the bear-defensive base, exactly like the bull overlay."""
    RECOVERY = {"profit_take_gain_pct": 100, "entry_extension_block_pct": 25,
                "momentum_partial_trim_execution_enabled": True}
    cfg = {**BASE, "regime_profiles": {"bull": BULL, "recovery": RECOVERY}}
    out = apply_regime_profile(cfg, "recovery")
    assert out is not cfg
    assert out["profit_take_gain_pct"] == 100          # capture lever wins
    assert out["entry_extension_block_pct"] == 25       # moderate ext-block (no CAR)
    assert out["momentum_partial_trim_execution_enabled"] is True
    # base-only transition levers survive
    assert out["regime_upgrade_confirm_bars"] == 3
    # a config WITHOUT a recovery overlay still maps recovery -> base (identity)
    assert apply_regime_profile(BASE, "recovery") is BASE
