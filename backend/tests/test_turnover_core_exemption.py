"""The core must stay turnover-exempt through the bear transition.

`core_sleeve_enabled` is regime-scoped in practice — doc-193 sets it only inside
regime_profiles.{bull,chop,recovery}, and _apply_regime_profile merges the
matching overlay into the spec before the sleeve reads it. There is no bear
profile, so in a bear the key is absent and the core stopped being exempt at
precisely the most expensive moment: releasing a ~60%-of-NAV core and deploying
a 35% hedge leg books ~95% of NAV of one-way notional against a 50% budget,
which then refuses every discretionary stock buy for the following 21 sessions —
the entire recovery leg off the bottom.
"""
import ast
import os
import types

BROKER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "broker.py")

WANTED = {"_turnover_is_governed", "_core_sleeve_cfg_raw"}


def _load():
    with open(BROKER, "r") as fh:
        tree = ast.parse(fh.read())
    keep = [n for n in tree.body if getattr(n, "name", None) in WANTED]
    mod = types.ModuleType("_tg")
    exec(compile(ast.Module(body=keep, type_ignores=[]), BROKER, "exec"), mod.__dict__)
    return mod


MOD = _load()


def _specs(cfg):
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


BULL_ONLY = {
    "residual_sleeve_symbol": "SPY",
    "regime_profiles": {
        "bull": {"core_sleeve_enabled": True},
        "chop": {"core_sleeve_enabled": True},
        "recovery": {"core_sleeve_enabled": True},
    },
}


def test_core_is_exempt_in_bear_when_only_profiles_arm_it():
    """THE bug: no bear profile, so the merged config has no core_sleeve_enabled."""
    merged = dict(BULL_ONLY)          # bear: no overlay merged in
    assert MOD._turnover_is_governed("SPY", _specs(merged)) is False, (
        "the core was governed during the bear transition — its release will "
        "consume the whole budget and starve the recovery")


def test_core_is_exempt_in_bull_where_the_overlay_did_merge():
    merged = dict(BULL_ONLY, core_sleeve_enabled=True)
    assert MOD._turnover_is_governed("SPY", _specs(merged)) is False


def test_the_hedge_leg_is_still_governed():
    """Only the core is the low-turnover baseline. SQQQ is discretionary."""
    assert MOD._turnover_is_governed("SQQQ", _specs(dict(BULL_ONLY))) is True


def test_satellite_names_are_still_governed():
    for sym in ("SNDK", "MU", "AAPL"):
        assert MOD._turnover_is_governed(sym, _specs(dict(BULL_ONLY))) is True, sym


def test_everything_is_governed_when_the_core_is_off_everywhere():
    cfg = {"residual_sleeve_symbol": "SPY", "regime_profiles": {"bull": {}}}
    assert MOD._turnover_is_governed("SPY", _specs(cfg)) is True


def test_no_regime_profiles_at_all_governs_everything():
    assert MOD._turnover_is_governed("SPY", _specs({})) is True


def test_a_custom_core_symbol_is_honoured():
    cfg = dict(BULL_ONLY, residual_sleeve_symbol="VTI")
    assert MOD._turnover_is_governed("VTI", _specs(cfg)) is False
    assert MOD._turnover_is_governed("SPY", _specs(cfg)) is True


def test_malformed_profiles_fail_safe_to_governed():
    for bad in ("nonsense", 42, ["bull"], {"bull": "yes"}, {"bull": None}):
        cfg = {"residual_sleeve_symbol": "SPY", "regime_profiles": bad}
        assert MOD._turnover_is_governed("SPY", _specs(cfg)) is True, bad


def test_case_and_whitespace_are_normalised():
    assert MOD._turnover_is_governed("  spy ", _specs(dict(BULL_ONLY))) is False
