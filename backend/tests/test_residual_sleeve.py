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
_WANTED = {
    "_residual_sleeve_config",
    "_chop_ret20_cfg",
    "_residual_sleeve_release",
    "_residual_sleeve_deploy",
    # 2026-08-05: the sleeve reads unfilled orders off the execution simulator to
    # size around in-flight clips. It is CALLED BY the two functions above, so it
    # must be extracted too or they NameError inside the stub namespace.
    "_sleeve_pending_qty",
    "_submit_portfolio_signal",
    "_signal_result_is_confirmed",
}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
b = types.SimpleNamespace()
_ns = {
    # Booking gate: real impl exempts the core symbol so the core's own
    # establishment cannot exhaust the discretionary budget. True = book it,
    # which is today's behaviour and what these legacy tests assert.
    "_turnover_is_governed": lambda *_a, **_k: True,
    "math": __import__("math"),
    "_log": lambda *a, **k: None,
    "_RESIDUAL_SLEEVE_STATE": {"last_park_ts": None, "bear_entry_px": None,
                               "last_bear_exit_ts": None},
    "_sleeve_market_regime": lambda: "bull",
    "_sleeve_circuit_tier": lambda: "",
    # Rally-onset suppressor (default OFF in production). Both
    # _residual_sleeve_deploy and _regime_position_cap_hard consult it, so it
    # must be stubbed here or they raise NameError under the AST harness.
    "_sleeve_rally_onset": lambda: False,
}
# Module-level constants the extracted functions close over. Pulled from the
# source rather than hardcoded so a change to the real floor shows up here.
_WANTED_CONSTS = {"_RESIDUAL_SLEEVE_MIN_RELEASE_USD"}
# 2026-08-03: the index core is a FAMILY of broker helpers that the sleeve
# functions call (_core_sleeve_cfg, the turnover ledger, ...). Extract them by
# prefix rather than by name. The sleeve bodies are wrapped in a bare
# `except Exception`, so a helper missing from this namespace does not raise —
# it logs "deploy skipped" in yellow and every assertion in this file silently
# tests a no-op. Prefix matching means adding one more helper cannot reopen
# that hole.
_CORE_PREFIXES = ("_core_sleeve", "_core_turnover", "_turnover_ledger", "_CORE_")


def _is_core_family(name):
    return any(str(name).startswith(p) for p in _CORE_PREFIXES)


for _node in _tree.body:
    if isinstance(_node, ast.Assign) and any(
        isinstance(t, ast.Name) and (t.id in _WANTED_CONSTS or _is_core_family(t.id))
        for t in _node.targets
    ):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _name in _WANTED_CONSTS:
    assert _name in _ns, f"failed to extract {_name} from broker.py"
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and (
            _node.name in _WANTED or _is_core_family(_node.name)):
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
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = None
    b._RESIDUAL_SLEEVE_STATE["last_bear_exit_ts"] = None
    b._RESIDUAL_SLEEVE_STATE["bear_stop_episode"] = False
    b._RESIDUAL_SLEEVE_STATE["bear_alloc_ratchet"] = 0.0
    b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] = None
    # 2026-08-04: same-bar deploy accounting. Module-level state keyed on the
    # bar timestamp, so without this reset one test's committed notional shrinks
    # the next test's `room` whenever they share a timestamp.
    b._RESIDUAL_SLEEVE_STATE["bear_pending_deploy"] = None
    b._RESIDUAL_SLEEVE_STATE["bear_pending_refill"] = None
    b._ns["_sleeve_circuit_tier"] = lambda: ""
    b._ns.pop("_strategy_cache", None)  # churn-fix tests inject dwell here
    _set_regime("bull")


def _set_dwell(n):
    """Inject the shared confirmed-bear dwell the conviction refill gate reads
    (globals() inside the extracted fn resolves to _ns)."""
    b._ns["_strategy_cache"] = {"graph_nexus_analysis": {"_bear_dwell_bars": int(n)}}


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
_WANTED2 = {"_regime_position_cap_hard", "_regime_recovery_hard_cap",
            "_nexus_recovery_flag", "_nexus_bear_capacity_latch"}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED2:
        _mod = ast.Module(body=[_node], type_ignores=[])
        exec(compile(_mod, "broker.py", "exec"), _ns)
for _w in _WANTED2:
    assert _w in _ns, _w
    setattr(b, _w, _ns[_w])

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


# ── 2026-07-28 A1: recovery hard-cap coherence (default OFF) ──
def _set_recovery(flag=False, latch=False):
    """Stub the two nexus strategy-cache reads the recovery cap depends on."""
    b._ns["_nexus_recovery_flag"] = lambda: flag
    b._ns["_nexus_bear_capacity_latch"] = lambda: latch


def _rec_spec(**cfg):
    base = {"max_positions": 14, "max_positions_bull": 14,
            "max_positions_chop": 8, "max_positions_bear": 2,
            "regime_position_cap_recovery_hard_enforce": True,
            "max_positions_recovery": 14}
    base.update(cfg)
    return [{"strategy": "graph_nexus_analysis", "config": base}]


def test_recovery_cap_default_off_is_byte_compatible():
    """Absent flag => the ordinary chop cap, even in a confirmed recovery."""
    _set_regime("chop")
    _set_recovery(flag=True)
    spec = _rec_spec()
    spec[0]["config"].pop("regime_position_cap_recovery_hard_enforce")
    assert b._regime_position_cap_hard(spec) == ("chop", 8)
    assert b._regime_position_cap_hard(
        _rec_spec(regime_position_cap_recovery_hard_enforce=False)) == ("chop", 8)


def test_recovery_cap_raises_cap_when_confirmed():
    _set_regime("chop")
    _set_recovery(flag=True)
    assert b._regime_position_cap_hard(_rec_spec()) == ("recovery", 14)


def test_recovery_cap_requires_recovery_flag_and_chop():
    _set_regime("chop")
    _set_recovery(flag=False)
    assert b._regime_position_cap_hard(_rec_spec()) == ("chop", 8)
    # A recovery flag never overrides a non-chop confirmed regime.
    _set_recovery(flag=True)
    for regime, cap in (("bull", 14), ("bear", 2), ("crash", 0)):
        _set_regime(regime)
        assert b._regime_position_cap_hard(_rec_spec()) == (regime, cap)


def test_recovery_cap_yields_to_bear_capacity_latch():
    """The latch holds the bear cap through chop interludes in a downtrend."""
    _set_regime("chop")
    _set_recovery(flag=True, latch=True)
    assert b._regime_position_cap_hard(_rec_spec()) == ("chop", 8)


def test_recovery_cap_invalid_config_falls_back_to_chop():
    _set_regime("chop")
    _set_recovery(flag=True)
    for bad in (None, "abc", 0, -3, float("nan"), float("inf"), True, [14]):
        assert b._regime_position_cap_hard(
            _rec_spec(max_positions_recovery=bad)) == ("chop", 8), bad
    missing = _rec_spec()
    missing[0]["config"].pop("max_positions_recovery")
    assert b._regime_position_cap_hard(missing) == ("chop", 8)


def test_recovery_cap_clamps_to_max_positions_and_floors_at_chop():
    _set_regime("chop")
    _set_recovery(flag=True)
    # Never exceeds the global max_positions ceiling.
    assert b._regime_position_cap_hard(
        _rec_spec(max_positions_recovery=99)) == ("recovery", 14)
    # Never LOWERS the ordinary chop cap.
    assert b._regime_position_cap_hard(
        _rec_spec(max_positions_recovery=5)) == ("recovery", 8)


def test_cap_hard_none_when_no_regime_or_disabled():
    _set_regime("")
    assert b._regime_position_cap_hard(CAP_SPEC) is None
    _set_regime("bear")
    off = [{"strategy": "graph_nexus_analysis",
            "config": {"regime_position_cap_hard_enforce": False}}]
    assert b._regime_position_cap_hard(off) is None
    assert b._regime_position_cap_hard([{"strategy": "other", "config": {}}]) is None


# ── 2026-07-19: inverse-ETF bear leg (auto-buy in bear, auto-sell after) ──
BEAR_SPEC = [{"strategy": "graph_nexus_analysis", "config": {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "SQQQ",
    "residual_sleeve_bear_alloc_pct": 0.35,
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
}}]


class _Emu2(_Emu):
    """Emulator with an arbitrary positions map."""
    def __init__(self, cash, nav, positions=None):
        super().__init__(cash, nav)
        self._pos = dict(positions or {})

    def get_positions(self):
        return dict(self._pos)


def test_bear_leg_deploys_in_bear_capped_at_alloc():
    _set_regime("bear")
    # $6000 NAV, 100% cash: idle above 17% floor = 4980; cap 35% NAV = 2100.
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 3, 15), BEAR_SPEC)
    assert len(emu.signals) == 1
    assert emu.signals[0]["sym"] == "SQQQ"
    assert abs(emu.signals[0]["cash_per_trade"] - 2100.0) < 1e-6


def test_bear_leg_respects_existing_position_room():
    _set_regime("bear")
    # Already holding $1800 of SQQQ → room = 2100-1800 = 300 = 5% NAV min ✓
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 60.0})
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 4, 15), BEAR_SPEC)
    assert len(emu.signals) == 1
    assert abs(emu.signals[0]["cash_per_trade"] - 300.0) < 1e-6


def test_bear_leg_auto_sells_when_bear_over():
    _set_regime("chop")
    b._RESIDUAL_SLEEVE_STATE["last_park_ts"] = datetime(2026, 3, 20, 14)  # fresh park
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 28.0}, datetime(2026, 3, 20, 15), BEAR_SPEC)
    assert len(emu.signals) == 1
    assert emu.signals[0]["sym"] == "SQQQ"
    assert emu.signals[0]["sell_fraction"] == 1.0, "bear leg must fully auto-sell on upgrade"


def test_bear_leg_held_through_bear():
    _set_regime("bear")
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 28.0}, datetime(2026, 3, 20, 15), BEAR_SPEC)
    assert emu.signals == [], "bear leg holds while regime stays bear"


def test_no_bear_leg_in_chop_or_bull():
    for regime in ("chop", "bull", ""):
        _set_regime(regime)
        emu = _Emu2(cash=6000.0, nav=6000.0)
        b._residual_sleeve_deploy(emu, {"SQQQ": 30.0, "SPY": 600.0},
                                  datetime(2026, 3, 3, 15), BEAR_SPEC)
        assert all(s["sym"] != "SQQQ" for s in emu.signals), f"regime={regime!r}"


# ── 2026-07-19 adversarial-review fixes: stop-loss, dwell, refill ──
def test_bear_leg_stop_loss_exits_on_rally():
    _set_regime("bear")  # regime still bear — stop must fire anyway
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 30.0
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 26.5}, datetime(2026, 3, 21, 15), BEAR_SPEC)
    assert len(emu.signals) == 1 and emu.signals[0]["sell_fraction"] == 1.0
    assert b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] is None
    assert b._RESIDUAL_SLEEVE_STATE["last_bear_exit_ts"] is not None


def test_bear_leg_no_stop_within_band():
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 30.0
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 28.0}, datetime(2026, 3, 21, 15), BEAR_SPEC)
    assert emu.signals == []


def test_bear_leg_refill_when_cash_starved():
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 30.0
    # cash 5% of NAV < 15% release threshold → partial refill from the leg
    emu = _Emu2(cash=300.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 30.0}, datetime(2026, 3, 21, 15), BEAR_SPEC)
    assert len(emu.signals) == 1
    frac = emu.signals[0]["sell_fraction"]
    assert 0 < frac < 1.0, "refill must be partial, not a full exit"


def test_bear_redeploy_blocked_within_dwell_after_exit():
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["last_bear_exit_ts"] = datetime(2026, 3, 21, 14)
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 21, 16), BEAR_SPEC)
    assert emu.signals == [], "redeploy within min_park_hours of an exit must be blocked"


def test_bear_deploy_sets_entry_basis():
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = None
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 3, 15), BEAR_SPEC)
    assert abs(b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] - 30.0) < 1e-9


# ── 2026-07-24 hedge peak-banking trailing stop (bank the SQQQ leg's high) ──
TRAIL_SPEC = [{"strategy": "graph_nexus_analysis", "config": {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "SQQQ",
    "residual_sleeve_bear_alloc_pct": 0.35,
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
    "residual_sleeve_bear_hold_through_chop": True,        # trail must override this
    "residual_sleeve_bear_leg_trail_activation_pct": 10.0,  # arm at +10% from entry
    "residual_sleeve_bear_leg_trail_pct": 10.0,             # bank at -10% from peak
}}]


def _run_trail(prices_seq, regime="bear", entry=75.0, qty=50.0):
    """Feed a SQQQ price sequence through release() bar-by-bar; return the bar
    index where the leg was sold (or None). Peak tracks across bars."""
    _set_regime(regime)
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = entry
    b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] = None
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": qty})
    for i, px in enumerate(prices_seq):
        emu.signals = []
        b._residual_sleeve_release(emu, {"SQQQ": px}, datetime(2026, 3, 10 + i, 15), TRAIL_SPEC)
        if emu.signals:
            return i, emu.signals[0]
    return None, None


def test_trailing_stop_banks_the_peak_on_recovery():
    # entry 75 -> rises to 90 (armed at +10%=82.5) -> falls to 80 (< 90*0.9=81) -> BANK.
    i, sig = _run_trail([76, 82, 88, 90, 85, 80], entry=75.0)
    assert i == 5 and sig["sell_fraction"] == 1.0, "must fully bank when 10% below the 90 peak"


def test_trailing_stop_not_armed_holds_through_shallow_move():
    # entry 75, never rises past +10% (max 80 < 82.5) then dips: NOT armed -> hold.
    i, sig = _run_trail([76, 78, 80, 79, 77, 76], entry=75.0, regime="bear")
    assert i is None, "unarmed (peak never reached activation) -> no trailing exit"


def test_trailing_stop_holds_through_midbear_chop_then_continues():
    # entry 75 -> 80 -> dips to 77 (not armed, only +6.7% peak) -> resumes to 92:
    # a mid-bear pullback before the hedge is deep in profit must NOT bank.
    i, sig = _run_trail([78, 80, 77, 79, 85, 92], entry=75.0)
    assert i is None, "mid-bear chop below activation must not trip the trailing stop"


def test_trailing_stop_overrides_hold_through_chop():
    # In CHOP with hold_through_chop on, an armed+fallen leg still banks (the
    # trailing stop fires regardless of regime).
    i, sig = _run_trail([82, 90, 88, 80], entry=75.0, regime="chop")
    assert i == 3 and sig["sell_fraction"] == 1.0, "trail overrides hold-through-chop"


def test_trailing_stop_default_off_is_byte_identical():
    # activation/trail unset (0) -> no trailing exit even on a big peak->drop.
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 75.0
    b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] = None
    spec = [{"strategy": "graph_nexus_analysis", "config": {
        "residual_sleeve_enabled": True, "residual_sleeve_symbol": "SPY",
        "residual_sleeve_bear_symbol": "SQQQ", "residual_sleeve_bear_alloc_pct": 0.35,
        "residual_sleeve_buffer_pct": 0.02, "residual_sleeve_min_deploy_pct": 0.05,
        "residual_sleeve_release_cash_pct": 0.15,  # no trail keys -> default off
    }}]
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 50.0})
    for px in [82, 90, 80]:
        emu.signals = []
        b._residual_sleeve_release(emu, {"SQQQ": px}, datetime(2026, 3, 20, 15), spec)
    assert emu.signals == [], "default-off: no trailing exit"


def test_refill_full_liquidation_resets_trail_state():
    # bug-sweep 6b: a refill that sells the WHOLE (tiny) leg must reset entry+peak
    # so the next leg can't inherit this stale high and instantly self-destruct.
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 78.0
    b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] = 90.0
    emu = _Emu2(cash=50.0, nav=6000.0, positions={"SQQQ": 2.0})  # cash-starved, tiny leg
    b._residual_sleeve_release(emu, {"SQQQ": 80.0}, datetime(2026, 3, 21, 15), BEAR_SPEC)
    assert len(emu.signals) == 1 and abs(emu.signals[0]["sell_fraction"] - 1.0) < 1e-9
    assert b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] is None, "6b: full refill resets peak"
    assert b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] is None, "6b: full refill resets entry"


def test_trail_gap_through_stop_latches_episode():
    # bug-sweep 1b: a gap that banks BELOW the -10% entry stop is a stop-out —
    # latch one-stop-per-episode so deploy doesn't re-enter into the rally.
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 75.0
    b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] = 90.0
    b._RESIDUAL_SLEEVE_STATE["bear_stop_episode"] = False
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 50.0})
    b._residual_sleeve_release(emu, {"SQQQ": 66.0}, datetime(2026, 3, 21, 15), TRAIL_SPEC)
    assert len(emu.signals) == 1, "gap-down banks"
    assert b._RESIDUAL_SLEEVE_STATE["bear_stop_episode"] is True, "1b: gap-through-stop latches"


def test_full_leg_exit_resets_conviction_ratchet():
    # bt#869125: a trail-bank while regime stays bear left the 0.70 ratchet in
    # place, so the 04-02 re-park went max-size (70%) into the recovery (-$506).
    # Any full leg exit must reset the ratchet so a re-park re-earns its size.
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 75.0
    b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] = 90.0
    b._RESIDUAL_SLEEVE_STATE["bear_alloc_ratchet"] = 0.70  # carried from deep bear
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 50.0})
    b._residual_sleeve_release(emu, {"SQQQ": 80.0}, datetime(2026, 3, 21, 15), TRAIL_SPEC)
    assert len(emu.signals) == 1, "trail-banks (90 peak, 80 = -11%)"
    assert b._RESIDUAL_SLEEVE_STATE["bear_alloc_ratchet"] == 0.0, "full exit resets the ratchet"


def test_avg_down_repark_blends_peak():
    # bug-sweep 6c: an average-DOWN add blends the peak toward the add price so a
    # never-armed high can't retro-arm after the weighted entry drops.
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 100.0
    b._RESIDUAL_SLEEVE_STATE["bear_peak_px"] = 108.0
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 10.0})
    b._residual_sleeve_deploy(emu, {"SQQQ": 95.0}, datetime(2026, 3, 20, 15), TRAIL_SPEC)
    assert len(emu.signals) == 1, "should add to the leg"
    pk = b._RESIDUAL_SLEEVE_STATE["bear_peak_px"]
    assert pk is not None and 95.0 < pk < 108.0, f"6c: peak blended down (got {pk})"


# ── 2026-07-23 hold-through-chop: don't whipsaw the SQQQ hedge on chop ──
HOLD_CHOP_SPEC = [{"strategy": "graph_nexus_analysis", "config": {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "SQQQ",
    "residual_sleeve_bear_alloc_pct": 0.35,
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
    "residual_sleeve_bear_hold_through_chop": True,
}}]


def test_bear_leg_holds_through_chop_when_enabled():
    # regime downgraded to chop but cash is ample (no refill needed) → with the
    # flag ON the hedge is HELD, not dumped (was: full protective exit).
    _set_regime("chop")
    b._RESIDUAL_SLEEVE_STATE["last_park_ts"] = datetime(2026, 3, 6, 14)
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 75.0
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 27.0})
    b._residual_sleeve_release(emu, {"SQQQ": 72.0}, datetime(2026, 3, 6, 15), HOLD_CHOP_SPEC)
    assert emu.signals == [], "hold-through-chop: chop must NOT force-sell the hedge"


def test_bear_leg_still_exits_on_bull_with_hold_through_chop():
    # Only CHOP is held; a confirmed BULL still closes the leg.
    _set_regime("bull")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 75.0
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 27.0})
    b._residual_sleeve_release(emu, {"SQQQ": 72.0}, datetime(2026, 3, 20, 15), HOLD_CHOP_SPEC)
    assert len(emu.signals) == 1 and emu.signals[0]["sell_fraction"] == 1.0, \
        "confirmed bull must still fully exit the bear leg"


def test_leg_stop_loss_fires_while_holding_through_chop():
    # A held-through-chop position is STILL V-bottom protected: price below the
    # -10% leg stop fires a full exit even in chop (the elif->guarded-if change).
    _set_regime("chop")
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 80.0  # stop at 72.0
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 27.0})
    b._residual_sleeve_release(emu, {"SQQQ": 71.0}, datetime(2026, 3, 21, 15), HOLD_CHOP_SPEC)
    assert len(emu.signals) == 1 and emu.signals[0]["sell_fraction"] == 1.0, \
        "leg stop-loss must stay active on a held-through-chop bar"


# ── 2026-07-23 churn fix: conviction refill mirrors the deploy deep floor ──
# bt#336180 did 27 ping-pong SQQQ sells because deploy parked cash down to the
# deep floor (7%) while release trimmed back to the un-deepened 15%. The refill
# must use the SAME deep floor + gate (scale on AND deep>0 AND dwell>=min_days).
DEEP_SPEC = [{"strategy": "graph_nexus_analysis", "config": {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "SQQQ",
    "residual_sleeve_bear_alloc_pct": 0.35,
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
    "residual_sleeve_bear_alloc_scale_enabled": True,
    "residual_sleeve_release_cash_pct_deep": 0.05,
    "residual_sleeve_bear_scale_min_days": 3,
}}]


def test_conviction_refill_no_churn_at_deep_floor():
    # THE regression test. Sustained bear (dwell=5>=3), scale on, deep=0.05.
    # cash = 7% of NAV = $420, which is ABOVE the deep 5% floor ($300) but was
    # below the old 15% floor ($900). Pre-fix: sold SQQQ to reach 15% (churn).
    # Post-fix: effective floor is 5% → cash already sufficient → NO sell.
    _set_regime("bear")
    _set_dwell(5)
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 30.0  # avoid the stop-loss path
    emu = _Emu2(cash=420.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 30.0}, datetime(2026, 3, 21, 15), DEEP_SPEC)
    assert emu.signals == [], "deep-mode refill must NOT re-sell a fresh park (no ping-pong)"


def test_conviction_refill_deep_floor_still_refills_when_truly_starved():
    # Below even the deep floor: cash 3% ($180) < 5% ($300) → partial refill
    # sized to the DEEP target, not 15%.
    _set_regime("bear")
    _set_dwell(5)
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 30.0
    emu = _Emu2(cash=180.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 30.0}, datetime(2026, 3, 21, 15), DEEP_SPEC)
    assert len(emu.signals) == 1
    frac = emu.signals[0]["sell_fraction"]
    # needed = 0.05*6000 - 180 = 120 → 120/30 = 4 sh → 4/70 ≈ 0.057
    assert 0.0 < frac < 0.10, "deep refill sizes to the 5% target, not 15%"


def test_conviction_refill_before_dwell_uses_shallow_floor():
    # Same deep config but dwell=2 < min_days → deep NOT engaged → legacy 15%
    # floor: cash 7% ($420) < 15% ($900) → refill fires (matches bear_v3).
    _set_regime("bear")
    _set_dwell(2)
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 30.0
    emu = _Emu2(cash=420.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 30.0}, datetime(2026, 3, 21, 15), DEEP_SPEC)
    assert len(emu.signals) == 1, "before the dwell gate, the shallow 15% floor applies"


def test_conviction_refill_scale_off_is_byte_identical():
    # Deep value present but scale DISABLED (default) → shallow 15% floor:
    # the fix is default-off. cash 7% ($420) < 15% ($900) → refill fires.
    _set_regime("bear")
    _set_dwell(5)  # dwell high, but scale off → deep must not engage
    b._RESIDUAL_SLEEVE_STATE["bear_entry_px"] = 30.0
    off_spec = [{"strategy": "graph_nexus_analysis", "config": {
        "residual_sleeve_enabled": True,
        "residual_sleeve_symbol": "SPY",
        "residual_sleeve_bear_symbol": "SQQQ",
        "residual_sleeve_bear_alloc_pct": 0.35,
        "residual_sleeve_buffer_pct": 0.02,
        "residual_sleeve_min_deploy_pct": 0.05,
        "residual_sleeve_release_cash_pct": 0.15,
        "residual_sleeve_release_cash_pct_deep": 0.05,  # set, but scale off
    }}]
    emu = _Emu2(cash=420.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 30.0}, datetime(2026, 3, 21, 15), off_spec)
    assert len(emu.signals) == 1, "scale disabled → legacy 15% refill (byte-identical)"


# ── 2026-07-19 BULL_F7e fix: one stop-out per bear episode ──
def test_one_stop_per_bear_episode():
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["bear_stop_episode"] = True  # stopped out earlier
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 4, 2, 15), BEAR_SPEC)
    assert emu.signals == [], "no re-deploy after a stop-out in the same episode"


def test_episode_latch_rearms_on_regime_upgrade():
    b._RESIDUAL_SLEEVE_STATE["bear_stop_episode"] = True
    _set_regime("chop")  # bear over → latch re-arms
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 4, 8, 15), BEAR_SPEC)
    assert emu.signals == []  # chop never deploys the bear leg
    assert b._RESIDUAL_SLEEVE_STATE["bear_stop_episode"] is False
    _set_regime("bear")  # NEW bear episode → deploys again
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 4, 20, 15), BEAR_SPEC)
    assert len(emu.signals) == 1


# --- 2026-07-30 bear-persistence gate (residual_sleeve_bear_min_dwell_days) ---
#
# 17 of 19 backtests on the 2026-03-30..04-27 bull window parked 35% of NAV in
# SQQQ on DAY 1 of a +12.8% month and were stopped out for a deterministic
# -3.94pp. The regime label was legitimately "bear" there (ret20 -7.4%, at the
# 20-day low) and its ret5 was MORE negative than any bar of the genuine
# 2026-03-02..03-30 bear window, so no price threshold separates them.
# Persistence does: that "bear" lasted 4 days, the real one 21.

def _bear_spec_with_dwell(days):
    cfg = dict(BEAR_SPEC[0]["config"])
    cfg["residual_sleeve_bear_min_dwell_days"] = days
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


def test_dwell_gate_absent_is_byte_identical():
    _set_regime("bear")
    _set_dwell(0)
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 3, 15), BEAR_SPEC)
    assert len(emu.signals) == 1
    assert abs(emu.signals[0]["cash_per_trade"] - 2100.0) < 1e-6


def test_dwell_gate_blocks_a_brand_new_bear():
    _set_regime("bear")
    _set_dwell(1)
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 3, 15),
                              _bear_spec_with_dwell(5))
    assert emu.signals == [], "must not hedge into a 1-day-old bear"


def test_dwell_gate_allows_a_sustained_bear():
    _set_regime("bear")
    _set_dwell(6)
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 10, 15),
                              _bear_spec_with_dwell(5))
    assert len(emu.signals) == 1
    assert abs(emu.signals[0]["cash_per_trade"] - 2100.0) < 1e-6


def test_dwell_gate_boundary_is_inclusive():
    _set_regime("bear")
    _set_dwell(5)
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 10, 15),
                              _bear_spec_with_dwell(5))
    assert len(emu.signals) == 1, "dwell == threshold must deploy"


def test_dwell_gate_missing_cache_is_conservative():
    """No dwell state (cache absent) reads as 0 and must BLOCK, not deploy."""
    _set_regime("bear")
    b._ns.pop("_strategy_cache", None)
    emu = _Emu2(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SQQQ": 30.0}, datetime(2026, 3, 3, 15),
                              _bear_spec_with_dwell(5))
    assert emu.signals == []


def test_dwell_gate_does_not_touch_exits():
    """The gate is deploy-only: a held leg still auto-sells on a regime upgrade
    even while the dwell would have blocked a fresh park."""
    _set_regime("chop")
    _set_dwell(0)
    b._RESIDUAL_SLEEVE_STATE["last_park_ts"] = datetime(2026, 3, 20, 14)
    emu = _Emu2(cash=3000.0, nav=6000.0, positions={"SQQQ": 70.0})
    b._residual_sleeve_release(emu, {"SQQQ": 28.0}, datetime(2026, 3, 20, 15),
                               _bear_spec_with_dwell(5))
    assert len(emu.signals) == 1
    assert emu.signals[0]["sell_fraction"] == 1.0


# --- 2026-07-30 chop beta floor (residual_sleeve_chop_enabled) ---
#
# A rally off a bottom reads "chop" for most of its length. bt#426579 left
# $4,897 idle for five straight days while SPY ran, for a measured -3.28pp of
# cash drag. The sleeve already parks idle cash in SPY; it just waits for a
# bull confirmation that arrives near the end of the move.

def _spec_chop(enabled):
    cfg = dict(SPEC[0]["config"])
    cfg["residual_sleeve_chop_enabled"] = enabled
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


def test_chop_floor_off_by_default_is_byte_identical():
    _set_regime("chop")
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 4, 7, 15), SPEC)
    assert emu.signals == [], "default must not deploy in chop"


def test_chop_floor_deploys_when_enabled():
    _set_regime("chop")
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 4, 7, 15),
                              _spec_chop(True))
    assert len(emu.signals) == 1
    assert emu.signals[0]["sym"] == "SPY"
    # Same park-floor arithmetic as bull: 1800 - (15%+2%)*6000 = 780.
    assert abs(emu.signals[0]["cash_per_trade"] - 780.0) < 1e-6


def test_chop_floor_still_never_deploys_in_bear_or_crash():
    """The beta floor must not put long beta on in a downtrend."""
    for regime in ("bear", "crash"):
        _set_regime(regime)
        emu = _Emu(cash=1800.0, nav=6000.0)
        # No bear_symbol configured in SPEC, so the bear leg cannot absorb it —
        # this proves the chop flag alone never opens a long in bear/crash.
        b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 10, 15),
                                  _spec_chop(True))
        assert emu.signals == [], f"chop floor must stay out of regime={regime!r}"


def test_chop_floor_bull_behaviour_unchanged():
    _set_regime("bull")
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 4, 22, 15),
                              _spec_chop(True))
    assert len(emu.signals) == 1
    assert abs(emu.signals[0]["cash_per_trade"] - 780.0) < 1e-6


def test_chop_floor_protective_exit_still_full_on_downgrade():
    """Parked in chop, then the regime rolls to bear: exit must be full."""
    _set_regime("bear")
    b._RESIDUAL_SLEEVE_STATE["last_park_ts"] = datetime(2026, 4, 7, 14)
    emu = _Emu(cash=3000.0, nav=6000.0, sleeve_qty=3.0)
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 4, 8, 15),
                               _spec_chop(True))
    assert len(emu.signals) == 1
    assert emu.signals[0]["sell_fraction"] == 1.0


# --- 2026-08-02: chop parking gated on the 20-day proxy return ---
#
# Bull-window chop is a rally being confirmed; parking there earns. Bear-window
# chop is an interlude between down legs — where 19 of 19 bear-leg entries were
# opened — and parking there spends the cash that funds the SQQQ hedge.
# Measured: bear went +10.17% -> +3.61% with chop parking on.

def _spec_chop_ret20(min_ret20):
    cfg = dict(SPEC[0]["config"])
    cfg["residual_sleeve_chop_enabled"] = True
    if min_ret20 is not None:
        cfg["residual_sleeve_chop_min_ret20_pct"] = min_ret20
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


def _set_ret20(v):
    b._ns["_strategy_cache"] = {"graph_nexus_analysis": {"_market_regime_diag": {"ret20": v}}}


def test_chop_park_blocked_when_ret20_negative():
    _set_regime("chop"); _set_ret20(-5.2)          # bear-adjacent chop
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 17, 15),
                              _spec_chop_ret20(0.0))
    assert emu.signals == [], "parked long beta on a bear-adjacent chop bar"


def test_chop_park_allowed_when_ret20_positive():
    _set_regime("chop"); _set_ret20(+3.4)          # rally being confirmed
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 4, 13, 15),
                              _spec_chop_ret20(0.0))
    assert len(emu.signals) == 1


def test_chop_ret20_gate_off_by_default():
    """Key absent -> today's behaviour, parks regardless of ret20."""
    _set_regime("chop"); _set_ret20(-5.2)
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 17, 15),
                              _spec_chop_ret20(None))
    assert len(emu.signals) == 1


def test_chop_gate_does_not_affect_bull_bars():
    _set_regime("bull"); _set_ret20(-5.2)          # gate must not touch bull
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 4, 22, 15),
                              _spec_chop_ret20(0.0))
    assert len(emu.signals) == 1


# ── Fail-closed: every ret20 failure mode used to fall toward PARKING, which is
# the exact behaviour the gate exists to prevent (-6.56pp on the bear window).
# The blind path in _detect_market_regime returns regime "chop" with ret20 still
# None whenever no proxy has >=21 point-in-time closes, so this is reachable.

def test_chop_park_blocked_when_ret20_missing():
    _set_regime("chop"); _set_ret20(None)
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 17, 15),
                              _spec_chop_ret20(0.0))
    assert emu.signals == [], "missing ret20 must not park (fail closed)"


def test_chop_park_blocked_when_ret20_nan():
    _set_regime("chop"); _set_ret20(float("nan"))
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 17, 15),
                              _spec_chop_ret20(0.0))
    assert emu.signals == [], "NaN ret20 compares False both ways; must not park"


def test_chop_park_blocked_when_strategy_cache_empty():
    _set_regime("chop")
    b._ns.pop("_strategy_cache", None)
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, datetime(2026, 3, 17, 15),
                              _spec_chop_ret20(0.0))
    assert emu.signals == [], "no diag at all must not park"


def test_blank_chop_ret20_string_does_not_disable_the_sleeve():
    """A cleared UI field arrives as "". float("") raises, and the raise
    escaped _residual_sleeve_config -- silently killing the whole sleeve at
    all three call sites, not just this one gate."""
    assert b._chop_ret20_cfg({"residual_sleeve_chop_min_ret20_pct": ""}) is None
    assert b._chop_ret20_cfg({"residual_sleeve_chop_min_ret20_pct": None}) is None
    assert b._chop_ret20_cfg({"residual_sleeve_chop_min_ret20_pct": "bogus"}) is None
    assert b._chop_ret20_cfg({"residual_sleeve_chop_min_ret20_pct": "2.5"}) == 2.5
    assert b._chop_ret20_cfg({}) is None


# ── 2026-08-02: minimum release size (backtest <-> live parity) ──
# Deploy has always floored at max($50, min_deploy_pct*NAV); release had no
# floor at all, so a $2 cash shortfall emitted a $2 sell. Alpaca rejects any
# order under $1 of notional, so those fills existed only in the emulator: an
# audit of the best-performing run found 219 of its 260 trades under $1.

def test_release_below_the_minimum_is_skipped():
    # cash $1 short of the 15% target -> a $1.00 sell, which live rejects.
    emu = _Emu(cash=899.0, nav=6000.0, sleeve_qty=3.0)
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 3, 3, 15), SPEC)
    assert emu.signals == [], "a sub-minimum release must not be emitted"


def test_release_at_the_minimum_still_executes():
    # $6 short -> a $6.00 sell, above the $5 floor.
    emu = _Emu(cash=894.0, nav=6000.0, sleeve_qty=3.0)
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 3, 3, 15), SPEC)
    assert len(emu.signals) == 1
    notional = emu.signals[0]["sell_fraction"] * 3.0 * 600.0
    assert notional >= b._ns["_RESIDUAL_SLEEVE_MIN_RELEASE_USD"]


def test_the_floor_is_comfortably_above_the_broker_minimum():
    """Alpaca's real floor is $1. The release is priced off THIS bar and fills
    on the next, so a $1.01 decision can slip under it before submission."""
    assert b._ns["_RESIDUAL_SLEEVE_MIN_RELEASE_USD"] >= 2.0


def test_protective_exit_of_a_dust_position_is_also_skipped():
    """Protective exits are NOT exempt: live cannot fill a $3 sell either, and
    exempting them would put the divergence straight back."""
    _set_regime("bear")
    emu = _Emu(cash=3000.0, nav=6000.0, sleeve_qty=0.005)  # $3 of SPY
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 3, 3, 15), SPEC)
    assert emu.signals == []


def test_protective_exit_above_the_minimum_is_untouched():
    _set_regime("bear")
    emu = _Emu(cash=3000.0, nav=6000.0, sleeve_qty=3.0)
    b._residual_sleeve_release(emu, {"SPY": 600.0}, datetime(2026, 3, 3, 15), SPEC)
    assert len(emu.signals) == 1
    assert emu.signals[0]["sell_fraction"] == 1.0
