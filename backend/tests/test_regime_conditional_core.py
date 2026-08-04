"""Regime-CONDITIONAL index core: arm the core in bull, fall through to the
bear-defensive base (and its SQQQ hedge) everywhere else.

WHY THIS SHAPE EXISTS
---------------------
Two measured configs each win exactly where the other loses, on the same build
(d0fe242ae4), same windows, isolated history_scope_salt per arm:

    window                     control (doc-185)   4-key cut (doc-184)
    bull 2026-03-30..04-27     +2.30%  (SPY +12.79)  +16.02%
    bear 2026-03-02..03-30    +10.07%  (SPY -7.89)    -2.71%

The bear split is mechanical, not statistical: `core_sleeve_enabled` routes bear
de-risk to CASH and never parks the inverse leg, so arming it costs the SQQQ
hedge that IS the control's entire bear result (SQQQ +$676 vs holding SPY -$165).

So the core must be ON in bull and OFF in bear. `_apply_regime_profile` already
merges `regime_profiles[regime]` into the SHARED spec config before the sleeve
reads it (broker.py: "before gna/the sleeve read the SHARED spec"), and
`core_sleeve_enabled` is NOT in `_REGIME_PROFILE_BASE_ONLY_KEYS`, so the overlay
is a legal home for it. These tests pin that contract, because it is the single
assumption the whole configuration rests on.

The transition case is the dangerous one and is tested explicitly: when bull ->
bear flips the core OFF, a ~60%-of-NAV SPY position is still held. It must not
become an unmanaged orphan.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# broker.py argparse-SystemExits under pytest — extract just the pure helpers.
_WANTED = {"_apply_regime_profile", "_core_sleeve_cfg"}
_WANTED_CONST = {"_REGIME_PROFILE_BASE_ONLY_KEYS"}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
_ns = {"_log": lambda *a, **k: None}
for _node in _tree.body:
    _take = (isinstance(_node, ast.FunctionDef) and _node.name in _WANTED) or (
        isinstance(_node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in _WANTED_CONST
                for t in _node.targets))
    if _take:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _w in _WANTED | _WANTED_CONST:
    assert _w in _ns, _w
apply_regime_profile = _ns["_apply_regime_profile"]
core_sleeve_cfg = _ns["_core_sleeve_cfg"]

#: The five core_* levers, exactly as measured on doc-184.
CORE_OVERLAY = {
    "core_sleeve_enabled": True,
    "core_target_pct": 0.6,
    "core_rebalance_band_pct": 0.05,
    "core_rebalance_min_days": 5,
    "core_bear_max_step_pct": 0.15,
}

#: doc-179's shape: bear-defensive base carrying the SQQQ residual sleeve, with
#: the churn levers in BASE (they never touch the hedge) and the core scoped to
#: the bull/recovery overlays only.
BASE = {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "SQQQ",
    "min_hold_enabled": True,
    "min_hold_days": 30,
    "rank_band_enabled": True,
    "rank_band_entry_pct": 10,
    "rank_band_exit_pct": 50,
    "turnover_budget_monthly_pct": 0.5,
    "regime_upgrade_confirm_bars": 3,
    "regime_profiles": {"bull": dict(CORE_OVERLAY), "recovery": dict(CORE_OVERLAY)},
}


def _spec(config):
    return [{"strategy": "graph_nexus_analysis", "config": config}]


# --- the load-bearing assumption -------------------------------------------

def test_core_sleeve_enabled_is_not_denylisted_from_overlays():
    """If this ever regresses the whole configuration silently reverts to
    'core never arms', which reads as 'the bull window got worse' with no error."""
    for key in CORE_OVERLAY:
        assert key not in _ns["_REGIME_PROFILE_BASE_ONLY_KEYS"], key
        assert not key.startswith("regime_")
        assert not key.startswith("max_positions")


def test_bull_overlay_arms_the_core():
    merged = apply_regime_profile(BASE, "bull")
    cfg = core_sleeve_cfg(_spec(merged))
    assert cfg is not None, "bull must arm the index core"
    assert cfg.enabled is True
    assert cfg.target_pct == 0.6
    assert cfg.rebalance_min_days == 5
    assert cfg.bear_max_step_pct == 0.15


def test_recovery_overlay_arms_the_core():
    """A confirmed recovery selects the 'recovery' profile, not 'bull'."""
    cfg = core_sleeve_cfg(_spec(apply_regime_profile(BASE, "recovery")))
    assert cfg is not None and cfg.enabled is True


def test_bear_falls_through_to_base_and_the_core_stays_off():
    """The bear result IS the SQQQ hedge. The core must not arm and steal it."""
    merged = apply_regime_profile(BASE, "bear")
    assert merged is BASE or "core_sleeve_enabled" not in merged
    assert core_sleeve_cfg(_spec(merged)) is None, \
        "core armed in bear would route de-risk to cash and drop the SQQQ leg"


def test_chop_and_crash_also_leave_the_core_off():
    for regime in ("chop", "crash", "", None):
        assert core_sleeve_cfg(_spec(apply_regime_profile(BASE, regime))) is None, regime


# --- the transition, which is where this design can actually hurt -----------

def test_bull_to_bear_transition_leaves_the_sleeve_owning_the_position():
    """Flipping the core OFF mid-book must not orphan the ~60%-of-NAV core.

    The core deliberately reuses `residual_sleeve_symbol`, so when it disarms
    the position is still the residual sleeve's parked leg — the sleeve's own
    release/bear-rotation path owns it. `residual_sleeve_enabled` staying true
    in BASE is what makes that true, so pin it.
    """
    bull = apply_regime_profile(BASE, "bull")
    bear = apply_regime_profile(BASE, "bear")
    assert core_sleeve_cfg(_spec(bull)) is not None
    assert core_sleeve_cfg(_spec(bear)) is None
    # The position does not become unmanaged: the sleeve is on in BOTH states
    # and owns `residual_sleeve_symbol` either way.
    assert bull["residual_sleeve_enabled"] is True
    assert bear["residual_sleeve_enabled"] is True
    assert bull["residual_sleeve_symbol"] == bear["residual_sleeve_symbol"] == "SPY"


def test_core_fails_closed_if_someone_drops_the_residual_sleeve():
    """`core_sleeve_enabled` without `residual_sleeve_enabled` must refuse to
    arm — without the sleeve the six _sleeve_symbols exemptions are empty and
    five independent lanes would sell the core on its first bar."""
    base = dict(BASE, residual_sleeve_enabled=False)
    merged = apply_regime_profile(base, "bull")
    assert core_sleeve_cfg(_spec(merged)) is None


# --- churn levers stay live in the bear, which is the point of arm 2 --------

def test_churn_levers_survive_into_every_regime():
    """min_hold / rank_band / turnover_budget live in BASE precisely so they
    cut churn in bear too, where the core-based cut REVERSED (32.1 -> 35.7x/yr)."""
    for regime in ("bull", "bear", "chop", "recovery"):
        merged = apply_regime_profile(BASE, regime)
        assert merged["min_hold_enabled"] is True, regime
        assert merged["rank_band_enabled"] is True, regime
        assert merged["turnover_budget_monthly_pct"] == 0.5, regime
