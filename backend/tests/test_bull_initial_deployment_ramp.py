"""A3 (2026-07-28): a confirmed-bull initial deployment ramp.

The global ramp throttles the first bars of every run to 50/70/90% of starting
equity. That is the right shape for an unknown or hostile tape, but it also
throttles the opening bars of a confirmed bull, and a global loosening was
already measured and REJECTED: `deployment_bar1_cap_pct` 0.9 -> 0.5 gained
+7.04pp on the rally window and cost -13.91pp on the flagship, a net -10.25pp.

So the ramp is made regime-scoped instead of globally retuned.
`deployment_ramp_caps_by_regime` is base-only and carries a single `bull`
entry of exactly three caps. ONLY a current confirmed bull selects it; recovery
(chop + flag), plain chop, bear, crash, unknown and any downgraded label keep
the legacy global list and the existing chop scaling. An invalid mapping fails
locally to the global list, so a malformed edit can never deploy faster than
the reviewed ramp.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g  # noqa: E402

# broker.py argparse-SystemExits under pytest -- extract just the pure helper.
_WANTED = {"_nexus_deployment_ramp_max_len"}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
_ns = {}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _w in _WANTED:
    assert _w in _ns, _w
_ramp_max_len = _ns["_nexus_deployment_ramp_max_len"]

_BULL = [0.50, 0.70, 1.00]
_GLOBAL = [0.50, 0.70, 0.90]


def _cfg(**kw):
    c = {"deployment_ramp_enabled": True,
         "deployment_ramp_caps_by_regime": {"bull": list(_BULL)}}
    c.update(kw)
    return c


def _cache(regime, recovery=False):
    return {"_market_regime": regime, "_market_regime_recovery": recovery}


# ------------------------------------------------------------ cap selection
def test_absent_mapping_is_byte_compatible():
    """No mapping => the existing global list, for every regime."""
    for regime in ("bull", "chop", "bear", "crash", "", None):
        assert g._get_deployment_ramp_caps({}, _cache(regime)) == _GLOBAL, regime
    # ...and the legacy single-argument call still works.
    assert g._get_deployment_ramp_caps({}) == _GLOBAL


def test_confirmed_bull_selects_the_bull_ramp():
    assert g._get_deployment_ramp_caps(_cfg(), _cache("bull")) == _BULL


def test_every_other_label_keeps_the_global_ramp():
    """Recovery, chop, bear, crash, unknown and downgrades are unchanged."""
    for regime, recovery in (("chop", True), ("chop", False), ("bear", False),
                             ("crash", False), ("", False), ("sideways", False),
                             (None, False)):
        assert g._get_deployment_ramp_caps(
            _cfg(), _cache(regime, recovery)) == _GLOBAL, (regime, recovery)


def test_missing_cache_keeps_the_global_ramp():
    assert g._get_deployment_ramp_caps(_cfg(), None) == _GLOBAL
    assert g._get_deployment_ramp_caps(_cfg()) == _GLOBAL


def test_explicit_global_override_still_wins_outside_bull():
    cfg = _cfg(deployment_ramp_caps=[0.25, 0.55])
    assert g._get_deployment_ramp_caps(cfg, _cache("chop")) == [0.25, 0.55]
    assert g._get_deployment_ramp_caps(cfg, _cache("bull")) == _BULL


# ------------------------------------------------------------- validation
def test_bull_entry_requires_exactly_three_caps():
    for bad in ([0.5, 0.7], [0.5, 0.7, 0.9, 1.0], [], [0.5]):
        assert g._get_deployment_ramp_caps(
            _cfg(deployment_ramp_caps_by_regime={"bull": bad}),
            _cache("bull")) == _GLOBAL, bad


def test_bull_entry_rejects_out_of_band_and_non_finite_caps():
    for bad in (0, -0.1, 1.5, float("nan"), float("inf"), "x", None, True, []):
        assert g._get_deployment_ramp_caps(
            _cfg(deployment_ramp_caps_by_regime={"bull": [0.5, 0.7, bad]}),
            _cache("bull")) == _GLOBAL, bad


def test_malformed_mapping_falls_back_locally():
    for bad in (None, [], "x", 0.5, {"bull": None}, {"bull": "0.5,0.7,1.0"},
                {"chop": [0.1, 0.2, 0.3]}):
        assert g._get_deployment_ramp_caps(
            _cfg(deployment_ramp_caps_by_regime=bad), _cache("bull")) == _GLOBAL, bad


def test_full_nav_bull_ramp_is_accepted():
    assert g._get_deployment_ramp_caps(
        _cfg(deployment_ramp_caps_by_regime={"bull": [1.0, 1.0, 1.0]}),
        _cache("bull")) == [1.0, 1.0, 1.0]


# ------------------------------------------------- bar index / budget path
class _Emu:
    def __init__(self, cash, initial, positions_value=0.0):
        self._cash = float(cash)
        self._initial_value = float(initial)
        self._positions = {}
        self._pv = float(positions_value)

    def get_cash(self):
        return self._cash


def _budget(regime, cache=None, **cfg_kw):
    cache = _cache(regime) if cache is None else cache
    cfg = _cfg(cash_reserve_floor_pct=0.0, **cfg_kw)
    _, meta = g._compute_available_buy_budget(
        _Emu(cash=10000.0, initial=10000.0), {}, {}, [], {},
        cfg, cache, None, "2026-04-01")
    return meta["ramp_cap_pct"]


def _budget_at_bar(regime, bar, **cfg_kw):
    """Advance the shared index to `bar` and return that bar's cap."""
    cache = _cache(regime)
    cfg = _cfg(cash_reserve_floor_pct=0.0, **cfg_kw)
    meta = {}
    for day in range(1, bar + 1):
        _, meta = g._compute_available_buy_budget(
            _Emu(cash=10000.0, initial=10000.0), {}, {}, [], {},
            cfg, cache, None, f"2026-04-{day:02d}")
    return meta["ramp_cap_pct"]


def test_budget_uses_the_bull_ramp_where_it_differs():
    """Bars 1-2 coincide with the global list; bar 3 is where the bull ramp
    actually diverges (1.00 vs the global 0.90)."""
    assert _budget_at_bar("bull", 3) == 1.00
    assert _budget_at_bar("bear", 3) == 0.90


def test_budget_uses_the_bull_ramp_on_bar_one():
    assert _budget("bull") == 0.50


def test_budget_keeps_chop_scaling_for_recovery_and_chop():
    """Recovery is a chop label, so it keeps the defensive chop scale."""
    chop = _budget("chop", cache=_cache("chop"))
    recovery = _budget("chop", cache=_cache("chop", True))
    assert chop == recovery == round(0.50 * 0.6, 4)


def test_bar_index_advances_at_most_once_per_bar():
    cache = _cache("bull")
    cfg = _cfg(cash_reserve_floor_pct=0.0)
    for _ in range(3):
        g._compute_available_buy_budget(
            _Emu(cash=10000.0, initial=10000.0), {}, {}, [], {},
            cfg, cache, None, "2026-04-01")
    assert cache["_deployment_bar_index"] == 1
    g._compute_available_buy_budget(
        _Emu(cash=10000.0, initial=10000.0), {}, {}, [], {},
        cfg, cache, None, "2026-04-02")
    assert cache["_deployment_bar_index"] == 2


def test_ramp_index_is_global_and_does_not_restart_on_a_later_bull():
    """A bull that arrives on bar 4 must not rewind the ramp to bar 1."""
    cache = _cache("chop")
    cfg = _cfg(cash_reserve_floor_pct=0.0)
    for day in ("01", "02", "03"):
        g._compute_available_buy_budget(
            _Emu(cash=10000.0, initial=10000.0), {}, {}, [], {},
            cfg, cache, None, f"2026-04-{day}")
    cache["_market_regime"] = "bull"
    _, meta = g._compute_available_buy_budget(
        _Emu(cash=10000.0, initial=10000.0), {}, {}, [], {},
        cfg, cache, None, "2026-04-04")
    assert cache["_deployment_bar_index"] == 4
    assert meta["ramp_cap_pct"] == 1.0, "past the ramp end -> ungated, not restarted"


# ------------------------------------------------- broker warm-boot length
def test_warm_boot_length_defaults_to_the_baked_three():
    assert _ramp_max_len(None) == 3
    assert _ramp_max_len([]) == 3
    assert _ramp_max_len([{"strategy": "other", "config": {}}]) == 3
    assert _ramp_max_len(
        [{"strategy": "graph_nexus_analysis", "config": {}}]) == 3


def test_warm_boot_length_covers_the_longest_configured_ramp():
    """Over-skipping is safe (index past the end disables the ramp);
    under-skipping leaves a warm book throttled, which is the F1b bug."""
    specs = [{"strategy": "graph_nexus_analysis", "config": {
        "deployment_ramp_caps": [0.4, 0.6, 0.8, 0.9, 1.0],
        "deployment_ramp_caps_by_regime": {"bull": [0.5, 0.7, 1.0]},
    }}]
    assert _ramp_max_len(specs) == 5


def test_warm_boot_length_considers_every_regime_entry():
    specs = [{"strategy": "graph_nexus_analysis", "config": {
        "deployment_ramp_caps_by_regime": {
            "bull": [0.5, 0.7, 1.0],
            "future_regime": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        },
    }}]
    assert _ramp_max_len(specs) == 6


def test_warm_boot_length_survives_malformed_config():
    for bad in ("x", 5, {"bull": "nope"}, None):
        specs = [{"strategy": "graph_nexus_analysis", "config": {
            "deployment_ramp_caps_by_regime": bad}}]
        assert _ramp_max_len(specs) == 3, bad
