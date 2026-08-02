"""Index-core allocation wired into the real order path (broker.py).

`test_core_sleeve.py` pins the arithmetic in `backend/core_sleeve.py`.
`test_core_sleeve_live_reachability.py` pins that the wiring is reachable in
both modes. This file pins what the WIRING does: that the master flag is a real
master flag, that the core replaces the cash-level trigger rather than sitting
next to it, and that the turnover budget binds on buys and never on sells.

broker.py is not import-safe (argparse at module scope SystemExits under
pytest), so this uses the established AST-extraction pattern.
"""
import ast
import os
import sys
import types
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_WANTED = {
    "_residual_sleeve_config",
    "_chop_ret20_cfg",
    "_residual_sleeve_release",
    "_residual_sleeve_deploy",
    "_submit_portfolio_signal",
    "_signal_result_is_confirmed",
    "_conviction_bear_alloc",
}
_CORE_PREFIXES = ("_core_sleeve", "_core_turnover", "_turnover_ledger", "_CORE_")
_WANTED_CONSTS = {"_RESIDUAL_SLEEVE_MIN_RELEASE_USD"}

_src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_ns = {
    "math": __import__("math"),
    "_log": lambda *a, **k: None,
    "_RESIDUAL_SLEEVE_STATE": {},
    "_sleeve_market_regime": lambda: "bull",
    "_sleeve_circuit_tier": lambda: "",
    "_sleeve_rally_onset": lambda: False,
    "_strategy_cache": {},
}


def _is_core_family(name):
    return any(str(name).startswith(p) for p in _CORE_PREFIXES)


for _node in _tree.body:
    if isinstance(_node, ast.Assign) and any(
        isinstance(t, ast.Name) and (t.id in _WANTED_CONSTS or _is_core_family(t.id))
        for t in _node.targets
    ):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and (
            _node.name in _WANTED or _is_core_family(_node.name)):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _name in _WANTED | _WANTED_CONSTS:
    assert _name in _ns, f"failed to extract {_name} from broker.py"
# Everything the core needs must have come across; a missing helper would be
# swallowed by the sleeve's bare `except Exception` and every assertion below
# would silently pass against a no-op.
for _name in ("_core_sleeve_cfg", "_core_sleeve_decide", "_core_turnover_state",
              "_turnover_ledger_record", "_turnover_ledger_rolling",
              "_core_sleeve_days_since_rebalance", "_core_sleeve_bear_dwell"):
    assert _name in _ns, f"failed to extract {_name} from broker.py"
b = types.SimpleNamespace(**{k: v for k, v in _ns.items() if not k.startswith("__")})
b._ns = _ns


# doc-179 as it actually is on the live instance: the sleeve on, SPY, no bear
# leg, and NO core_* keys at all.
LEGACY_CFG = {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
    "cash_reserve_floor_pct": 0.02,
}
LEGACY = [{"strategy": "graph_nexus_analysis", "config": dict(LEGACY_CFG)}]


def _core_spec(**over):
    cfg = dict(LEGACY_CFG, core_sleeve_enabled=True)
    cfg.update(over)
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


CORE = _core_spec()
NOW = datetime(2026, 4, 6, 15)


class _Emu:
    """Minimal book. NAV is supplied independently of the position marks on
    purpose — the core sizes the satellite as a NAV RESIDUAL, and the test for
    an unpriced holding depends on those two disagreeing."""

    def __init__(self, cash, nav, positions=None):
        self._cash = float(cash)
        self._nav = float(nav)
        self._positions = dict(positions or {})
        self.signals = []

    def get_cash(self):
        return self._cash

    def get_portfolio_value(self, prices=None):
        return self._nav

    def get_positions(self):
        return dict(self._positions)

    def execute_signal(self, sym, sig, px, timestamp=None, sell_fraction=None,
                       cash_per_trade=None):
        self.signals.append({"sym": sym, "sig": sig, "px": px,
                             "sell_fraction": sell_fraction,
                             "cash_per_trade": cash_per_trade})
        return True


def _set_regime(regime):
    b._ns["_sleeve_market_regime"] = lambda: regime


def _set_dwell(days):
    b._ns["_strategy_cache"] = {
        "graph_nexus_analysis": {"_bear_dwell_bars": int(days)}}


def setup_function(_fn):
    b._ns["_RESIDUAL_SLEEVE_STATE"].clear()
    b._ns["_strategy_cache"] = {}
    b._ns["_sleeve_circuit_tier"] = lambda: ""
    _set_regime("bull")


# ── master flag ────────────────────────────────────────────────────────────

def test_doc179_parses_to_no_core():
    """The live config has no core_* key, so the core must be OFF and every
    branch below it textually skipped. This is the ship-dark contract."""
    assert b._core_sleeve_cfg(LEGACY) is None
    assert b._core_sleeve_cfg([]) is None
    assert b._core_sleeve_cfg(None) is None


def test_the_core_refuses_to_arm_without_the_sleeve_flag():
    """The core's six sell-exemptions all come from `_sleeve_symbols`, which
    returns an EMPTY set unless `residual_sleeve_enabled`. A core armed without
    it is a large permanent position that five independent lanes can sell on its
    first bar — strictly worse than no core, so it must fail closed."""
    spec = [{"strategy": "graph_nexus_analysis",
             "config": dict(LEGACY_CFG, core_sleeve_enabled=True,
                            residual_sleeve_enabled=False)}]
    assert b._core_sleeve_cfg(spec) is None


def test_flag_on_parses_the_documented_defaults():
    cfg = b._core_sleeve_cfg(CORE)
    assert cfg is not None and cfg.enabled is True
    assert cfg.target_pct == 0.60
    assert cfg.rebalance_band_pct == 0.05
    assert cfg.rebalance_min_days == 5
    # The core does NOT invent a second cash floor; it reads the existing key.
    assert cfg.cash_floor_pct == 0.02
    # Turnover budget stays OFF even with the core on: enabling an allocation
    # rule must not silently arm a trade blocker.
    assert cfg.turnover_budget_monthly_pct == 0.0


def test_flag_off_keeps_the_legacy_cash_level_park():
    """The exact legacy number: park cash above (15% + 2%) of NAV."""
    emu = _Emu(cash=1800.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, LEGACY)
    assert len(emu.signals) == 1
    assert abs(emu.signals[0]["cash_per_trade"] - 780.0) < 1e-6


def test_flag_off_keeps_the_legacy_full_protective_liquidation():
    _set_regime("bear")
    emu = _Emu(cash=3000.0, nav=6000.0, positions={"SPY": 3.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, LEGACY)
    assert len(emu.signals) == 1
    assert emu.signals[0]["sell_fraction"] == 1.0


# ── the tilt rule replaces the cash-level trigger ──────────────────────────

def test_silent_graph_puts_the_whole_book_in_the_index():
    """No opinion means satellite == 0 means core == core_max. The default
    state of the book becomes fully invested in the market instead of 25% dead
    in cash — that IS the restructure."""
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert len(emu.signals) == 1
    # target 1 - 0.02 cash floor - 0 satellite = 0.98, clamped at core_max.
    assert abs(emu.signals[0]["cash_per_trade"] - 5880.0) < 1e-6


def test_a_full_satellite_leaves_the_core_at_target():
    """Satellite at 38% of NAV -> core target 60%, the design's 60/38/2."""
    emu = _Emu(cash=3720.0, nav=6000.0, positions={"AAPL": 22.8})
    b._residual_sleeve_deploy(emu, {"SPY": 600.0, "AAPL": 100.0}, NOW, CORE)
    assert len(emu.signals) == 1
    assert abs(emu.signals[0]["cash_per_trade"] - 3600.0) < 1e-6


def test_a_graph_buy_is_funded_by_the_index_not_by_cash():
    """A satellite buy the allocator already approved releases exactly its
    shortfall from the core — no more. Sizing a release off a CASH TARGET
    instead is what makes today's sleeve hand back more than the book asked for
    and re-park it on the next bar."""
    emu = _Emu(cash=60.0, nav=6000.0, positions={"SPY": 6.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, CORE,
                               None, 500.0)
    assert len(emu.signals) == 1
    sold = emu.signals[0]["sell_fraction"] * 6.0 * 600.0
    assert abs(sold - 440.0) < 1e-6  # 500 requested - 60 already in cash


def test_funding_bypasses_the_cadence():
    """The sleeve's original job. A cadence that starved the allocator would
    turn every 5-day window into a do-nothing window."""
    b._ns["_RESIDUAL_SLEEVE_STATE"]["last_core_rebalance_ts"] = NOW - timedelta(hours=1)
    emu = _Emu(cash=60.0, nav=6000.0, positions={"SPY": 6.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, CORE, None, 500.0)
    assert len(emu.signals) == 1


def test_funding_does_not_reset_the_cadence_clock():
    """Resetting it there would let one busy buy day freeze the weight band for
    five sessions."""
    emu = _Emu(cash=60.0, nav=6000.0, positions={"SPY": 6.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, CORE, None, 500.0)
    assert "last_core_rebalance_ts" not in b._ns["_RESIDUAL_SLEEVE_STATE"]


# ── the band + cadence is what collapses the turnover ──────────────────────

def test_inside_the_band_nothing_trades():
    """core at 57% vs a 60% target is a 3pp drift, inside the +-5pp band. This
    single rule is what replaces the 2pp CASH band that every stock-lane trade
    used to drag the sleeve across."""
    # $6,000 book: core 5.7 SPY @600 = 3420 (57%), satellite 24 AAPL @100 =
    # 2400 (40%), cash 180 (3%). Target = 1 - 0.02 - 0.40 = 58%, so the drift is
    # 1pp.
    emu = _Emu(cash=180.0, nav=6000.0, positions={"SPY": 5.7, "AAPL": 24.0})
    b._residual_sleeve_deploy(emu, {"SPY": 600.0, "AAPL": 100.0}, NOW, CORE)
    assert emu.signals == []


def test_outside_the_band_but_inside_the_cadence_nothing_trades():
    b._ns["_RESIDUAL_SLEEVE_STATE"]["last_core_rebalance_ts"] = NOW - timedelta(days=2)
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert emu.signals == []


def test_the_cadence_expires():
    b._ns["_RESIDUAL_SLEEVE_STATE"]["last_core_rebalance_ts"] = NOW - timedelta(days=6)
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert len(emu.signals) == 1


def test_a_cold_book_may_build_the_core_immediately():
    """No stamp means no cadence gate — a cold start has to be allowed to get
    invested or the core never exists."""
    assert b._core_sleeve_days_since_rebalance(NOW) is None
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert len(emu.signals) == 1


def test_an_unreadable_cadence_stamp_fails_toward_fewer_trades():
    """This is a turnover-REDUCTION control, so a clock it cannot read must
    block, never wave through."""
    b._ns["_RESIDUAL_SLEEVE_STATE"]["last_core_rebalance_ts"] = "not-a-datetime"
    assert b._core_sleeve_days_since_rebalance(NOW) == 0


def test_a_persisted_iso_stamp_still_differences():
    """strategy_cache_persistence may hand back a string; a stamp that could not
    be parsed would wedge the core shut forever via the rule above."""
    b._ns["_RESIDUAL_SLEEVE_STATE"]["last_core_rebalance_ts"] = \
        (NOW - timedelta(days=9)).isoformat()
    assert b._core_sleeve_days_since_rebalance(NOW) == 9


def test_a_deploy_stamps_the_cadence_clock():
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert b._ns["_RESIDUAL_SLEEVE_STATE"]["last_core_rebalance_ts"] == NOW


# ── regime behaviour ──────────────────────────────────────────────────────

def test_the_core_is_held_through_chop():
    """The legacy sleeve deploys in confirmed bull only, so the book sat in cash
    through the chop bars that make up most of a recovery."""
    _set_regime("chop")
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert len(emu.signals) == 1
    # ... and the legacy path, same config minus the flag, does nothing.
    legacy_emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(legacy_emu, {"SPY": 600.0}, NOW, LEGACY)
    assert legacy_emu.signals == []


def test_bear_derisk_is_bounded_not_a_liquidation():
    """Today's release does `sell_qty = qty; frac = 1.0` — 60pp of one-way
    notional in one bar, on a detector whose own comment says it lags a
    V-bottom by ~2 sessions. Halving costs 30pp and keeps the book tracking if
    the regime call was wrong."""
    _set_regime("bear")
    _set_dwell(5)
    emu = _Emu(cash=2400.0, nav=6000.0, positions={"SPY": 6.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, CORE)
    assert len(emu.signals) == 1
    frac = emu.signals[0]["sell_fraction"]
    assert frac < 1.0, "the bounded de-risk must not liquidate the core"
    # target = 0.60 * 0.5 = 30% of NAV = $1800; from $3600 that is a $1800 sell.
    assert abs(frac * 6.0 * 600.0 - 1800.0) < 1e-6


def test_bear_derisk_waits_for_persistence():
    """17 of 19 backtests parked a hedge on day 1 of a +12.8% month. No price
    threshold separated that bar from a real bear; persistence does."""
    _set_regime("bear")
    _set_dwell(1)
    emu = _Emu(cash=2400.0, nav=6000.0, positions={"SPY": 6.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, CORE)
    assert emu.signals == []


def test_bear_derisk_stamps_the_clock_so_re_entry_waits():
    """This is what caps regime round trips: the re-entry has to wait out
    core_rebalance_min_days."""
    _set_regime("bear")
    _set_dwell(5)
    emu = _Emu(cash=2400.0, nav=6000.0, positions={"SPY": 6.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, CORE)
    assert b._ns["_RESIDUAL_SLEEVE_STATE"]["last_core_rebalance_ts"] == NOW


def test_the_core_never_parks_the_inverse_hedge():
    """Bear de-risk routes to CASH. A -3x daily-rebalanced leg cost 1.67pp
    time-weighted on the bull window and needed six suppressors to stop losing
    money."""
    _set_regime("bear")
    _set_dwell(9)
    spec = _core_spec(residual_sleeve_bear_symbol="SQQQ")
    emu = _Emu(cash=3000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0, "SQQQ": 30.0}, NOW, spec)
    assert all(s["sym"] != "SQQQ" for s in emu.signals)


def test_the_drawdown_circuit_still_blocks_adding_to_the_core():
    b._ns["_sleeve_circuit_tier"] = lambda: "kill"
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert emu.signals == []


def test_no_core_price_means_no_core_decision():
    """The failure that hid BULL_F/BEAR_F entirely: an unpriced sleeve symbol
    silently no-ops. It must not instead size off a stale book."""
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {}, NOW, CORE)
    assert emu.signals == []


# ── the satellite is a NAV residual ───────────────────────────────────────

def test_an_unpriced_holding_does_not_inflate_the_core_target():
    """The one arithmetic error in this design that spends real money. Summing
    over individually priced positions would read this book as 0% satellite and
    buy the core up to 98%, on top of a book that is already fully invested."""
    # NAV 6000, cash 100, core 3600 (6 SPY @600) -> satellite must be 2300,
    # even though the satellite name has no bar this tick.
    emu = _Emu(cash=100.0, nav=6000.0, positions={"SPY": 6.0, "NVDA": 10.0})
    order = b._core_sleeve_decide(
        emu, {"SPY": 600.0}, NOW, CORE,
        b._residual_sleeve_config(CORE), b._core_sleeve_cfg(CORE))
    assert abs(order.target_weight - (1.0 - 0.02 - 2300.0 / 6000.0)) < 1e-9
    assert order.notional == 0.0  # 60% current vs 59.67% target: inside the band


# ── turnover ledger + budget ──────────────────────────────────────────────

def test_the_ledger_books_one_way_notional_per_session():
    b._turnover_ledger_record(NOW, 1000.0, "buy")
    b._turnover_ledger_record(NOW, 500.0, "sell")
    assert b._turnover_ledger_rolling(NOW) == 1500.0


def test_the_ledger_window_is_21_sessions():
    for i in range(40):
        b._turnover_ledger_record(NOW + timedelta(days=i), 100.0, "t")
    assert b._turnover_ledger_rolling(NOW + timedelta(days=39)) == 2100.0
    assert len(b._ns["_RESIDUAL_SLEEVE_STATE"]["turnover_ledger"]) == 21


def test_a_quiet_week_ages_the_window_out():
    """A ledger that only advanced when you traded would let one heavy day hold
    the budget shut forever."""
    b._turnover_ledger_record(NOW, 5000.0, "t")
    assert b._turnover_ledger_rolling(NOW) == 5000.0
    for i in range(1, 22):
        b._turnover_ledger_record(NOW + timedelta(days=i), 1.0, "t")
    assert b._turnover_ledger_rolling(NOW + timedelta(days=21)) == 21.0


def test_a_stopped_instance_does_not_keep_a_stale_budget_shut():
    """The session count alone is not enough. With the loop stopped no buckets
    are created, so 21 buckets can span months — and this ledger is persisted
    across restarts precisely because this host restarts often. Without the
    calendar backstop a restart after a long pause would resume with last
    quarter's notional still blocking every buy."""
    b._turnover_ledger_record(NOW, 5000.0, "t")
    assert b._turnover_ledger_rolling(NOW) == 5000.0
    assert b._turnover_ledger_rolling(NOW + timedelta(days=40)) == 0.0


def test_the_calendar_backstop_does_not_fire_inside_the_window():
    """31 days is 21 sessions at 5/7 plus slack, so in normal operation the
    session count is what binds and this backstop never truncates a live month."""
    b._turnover_ledger_record(NOW, 5000.0, "t")
    assert b._turnover_ledger_rolling(NOW + timedelta(days=30)) == 5000.0


def test_a_sleeve_trade_is_booked_into_the_ledger():
    """Every discretionary trade has to reach the ledger or the budget measures
    a fraction of the book and never binds."""
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, CORE)
    assert b._turnover_ledger_rolling(NOW) == 5880.0


def test_the_budget_is_off_by_default_even_with_the_core_on():
    blocked, used = b._core_turnover_state(CORE, 6000.0, NOW)
    b._turnover_ledger_record(NOW, 1_000_000.0, "t")
    blocked, used = b._core_turnover_state(CORE, 6000.0, NOW)
    assert blocked is False


def test_the_budget_binds_at_the_configured_fraction_of_nav():
    spec = _core_spec(turnover_budget_monthly_pct=0.50)
    b._turnover_ledger_record(NOW, 2999.0, "t")
    blocked, used = b._core_turnover_state(spec, 6000.0, NOW)
    assert blocked is False and abs(used - 0.49983) < 1e-4
    b._turnover_ledger_record(NOW, 2.0, "t")
    blocked, used = b._core_turnover_state(spec, 6000.0, NOW)
    assert blocked is True


def test_an_exhausted_budget_still_permits_a_core_sell():
    """A budget that traps you in a position is worse than the cost it saves."""
    spec = _core_spec(turnover_budget_monthly_pct=0.01)
    b._turnover_ledger_record(NOW, 99_000.0, "t")
    assert b._core_turnover_state(spec, 6000.0, NOW)[0] is True
    emu = _Emu(cash=60.0, nav=6000.0, positions={"SPY": 6.0})
    b._residual_sleeve_release(emu, {"SPY": 600.0}, NOW, spec, None, 500.0)
    assert len(emu.signals) == 1, "the budget blocked a reduce-only release"


def test_an_exhausted_budget_does_NOT_block_a_band_core_buy():
    """The core is exempt from the discretionary budget. This is the fix for
    the failure bt 152918 exhibited: establishing a 60% core costs ~60% of NAV
    in one-way turnover, more than a 50%/month budget allows on its own, so the
    budget blocked the buys the core needed to assemble itself. The book froze
    at ~50% invested with the core pinned to its 30% clamp floor, and the
    allocation the design exists to hold was unreachable.

    The core cannot run away without the budget: a +/-5pp band and a 5-day
    cadence hard-cap it at 4.2 rebalances a month.
    """
    spec = _core_spec(turnover_budget_monthly_pct=0.01)
    b._turnover_ledger_record(NOW, 99_000.0, "t")
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, spec)
    assert emu.signals, "an exhausted budget must not starve the index core"


def test_the_escape_hatch_restores_the_old_coupling():
    spec = _core_spec(turnover_budget_monthly_pct=0.01,
                      core_respects_turnover_budget=True)
    b._turnover_ledger_record(NOW, 99_000.0, "t")
    emu = _Emu(cash=6000.0, nav=6000.0)
    b._residual_sleeve_deploy(emu, {"SPY": 600.0}, NOW, spec)
    assert emu.signals == []


def test_the_satellite_lane_is_still_budgeted():
    """Exempting the core must not exempt the discretionary lane -- that is the
    whole point of the budget."""
    spec = _core_spec(turnover_budget_monthly_pct=0.01)
    b._turnover_ledger_record(NOW, 99_000.0, "t")
    blocked, _used = b._core_turnover_state(spec, 6000.0, NOW)
    assert blocked is True


def test_the_budget_is_inert_without_the_core():
    """The master flag gates the budget too — one switch, not two."""
    legacy_budget = [{"strategy": "graph_nexus_analysis",
                      "config": dict(LEGACY_CFG,
                                     turnover_budget_monthly_pct=0.01)}]
    b._turnover_ledger_record(NOW, 99_000.0, "t")
    assert b._core_turnover_state(legacy_budget, 6000.0, NOW) == (False, 0.0)
