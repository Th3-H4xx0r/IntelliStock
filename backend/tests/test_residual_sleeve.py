"""Residual sleeve hysteresis fix (2026-07-19 regime-safety spec, Phase 5).

Park floor sits above the release threshold (no per-bar round-trips),
release is demand-sized and honors a min-park duration, protective
bear/crash exit stays full and unconditional.
"""
import ast
import os
import sys
import types
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# broker.py is NOT import-safe (argparse at module level SystemExits under
# pytest) — use the established extraction pattern: pull just the sleeve
# functions out of the source and exec them into a stub namespace.
_WANTED = {"_residual_sleeve_config", "_residual_sleeve_release",
           "_residual_sleeve_deploy"}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
b = types.SimpleNamespace()
_ns = {
    "_log": lambda *a, **k: None,
    "_RESIDUAL_SLEEVE_STATE": {"last_park_ts": None},
    "_sleeve_market_regime": lambda: "bull",
}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        _mod = ast.Module(body=[_node], type_ignores=[])
        exec(compile(_mod, "broker.py", "exec"), _ns)
for _name in _WANTED:
    assert _name in _ns, f"failed to extract {_name} from broker.py"
    setattr(b, _name, _ns[_name])
b._RESIDUAL_SLEEVE_STATE = _ns["_RESIDUAL_SLEEVE_STATE"]
b._ns = _ns


SPEC = [{"strategy": "graph_nexus_analysis", "config": {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
}}]


class _Emu:
    def __init__(self, cash, nav, sleeve_qty=0.0):
        self._cash = cash
        self._nav = nav
        self._sleeve_qty = sleeve_qty
        self.signals = []

    def get_cash(self):
        return self._cash

    def get_portfolio_value(self, prices=None):
        return self._nav

    def get_positions(self):
        return {"SPY": self._sleeve_qty} if self._sleeve_qty > 0 else {}

    def execute_signal(self, sym, sig, px, timestamp=None, sell_fraction=None,
                       cash_per_trade=None):
        self.signals.append({"sym": sym, "sig": sig, "px": px,
                             "sell_fraction": sell_fraction,
                             "cash_per_trade": cash_per_trade})
        return True


def _set_regime(regime):
    b._ns["_sleeve_market_regime"] = lambda: regime


def setup_function(_fn):
    b._RESIDUAL_SLEEVE_STATE["last_park_ts"] = None
    _set_regime("bull")


def test_park_floor_keeps_cash_above_release_threshold():
    # $6000 NAV, 30% cash: parkable = cash - (15%+2%)*nav = 1800-1020 = 780.
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 2, 15), SPEC)
    assert len(emu.signals) == 1
    parked = emu.signals[0]["cash_per_trade"]
    assert abs(parked - 780.0) < 1e-6
    # Post-park cash = 1800-780 = 1020 = exactly 17% of NAV ≥ 15% release
    # threshold → the old immediate-release oscillation cannot trigger.
    assert (1800.0 - parked) / 6000.0 >= 0.15


def test_deploy_skipped_when_only_dry_powder_left():
    # 16% cash: below the 17% park floor → nothing to park.
    emu = _Emu(cash=960.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 2, 15), SPEC)
    assert emu.signals == []


def test_release_is_partial_demand_sized():
    # cash 2% of NAV, sleeve 3 shares @600. Needed to reach 15% = 780 → 1.3sh.
    emu = _Emu(cash=120.0, nav=6000.0, sleeve_qty=3.0)
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 3, 3, 15), SPEC)
    assert len(emu.signals) == 1
    frac = emu.signals[0]["sell_fraction"]
    assert 0.42 < frac < 0.44  # 1.3/3.0 ≈ 0.433 — not the whole sleeve


def test_release_blocked_within_min_park_duration():
    b._RESIDUAL_SLEEVE_STATE["last_park_ts"] = datetime(2026, 3, 3, 14)
    emu = _Emu(cash=120.0, nav=6000.0, sleeve_qty=3.0)
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 3, 3, 15), SPEC)
    assert emu.signals == [], "release within min_park_hours must be blocked"


def test_protective_exit_full_and_unconditional():
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["last_park_ts"] = datetime(2026, 3, 3, 14)
    emu = _Emu(cash=3000.0, nav=6000.0, sleeve_qty=3.0)  # cash NOT low
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 3, 3, 15), SPEC)
    assert len(emu.signals) == 1
    assert emu.signals[0]["sell_fraction"] == 1.0


def test_no_deploy_outside_bull():
    for regime in ("chop", "bear", "crash", ""):
        _set_regime(regime)
        emu = _Emu(cash=1800.0, nav=6000.0)
        b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 2, 15), SPEC)
        assert emu.signals == [], f"sleeve must not deploy in regime={regime!r}"


# ── 2026-07-19: airtight regime position cap helper (BEAR_F6 fix) ──
_WANTED2 = {"_regime_position_cap_hard"}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED2:
        _mod = ast.Module(body=[_node], type_ignores=[])
        exec(compile(_mod, "broker.py", "exec"), _ns)
assert "_regime_position_cap_hard" in _ns
b._regime_position_cap_hard = _ns["_regime_position_cap_hard"]

CAP_SPEC = [{"strategy": "graph_nexus_analysis", "config": {
    "max_positions": 14, "max_positions_bull": 14,
    "max_positions_chop": 8, "max_positions_bear": 2,
}}]


def test_cap_hard_returns_regime_cap():
    _set_regime("bear")
    assert b._regime_position_cap_hard(CAP_SPEC) == ("bear", 2)
    _set_regime("chop")
    assert b._regime_position_cap_hard(CAP_SPEC) == ("chop", 8)
    _set_regime("bull")
    assert b._regime_position_cap_hard(CAP_SPEC) == ("bull", 14)
    _set_regime("crash")
    assert b._regime_position_cap_hard(CAP_SPEC) == ("crash", 0)


def test_cap_hard_none_when_no_regime_or_disabled():
    _set_regime("")
    assert b._regime_position_cap_hard(CAP_SPEC) is None
    _set_regime("bear")
    off = [{"strategy": "graph_nexus_analysis",
            "config": {"regime_position_cap_hard_enforce": False}}]
    assert b._regime_position_cap_hard(off) is None
    assert b._regime_position_cap_hard([{"strategy": "other", "config": {}}]) is None
