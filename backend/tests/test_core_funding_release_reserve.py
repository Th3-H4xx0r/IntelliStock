"""fix-core-recycle — a core funding release must not be re-bought as index.

THE DEFECT (docs/investigations/sweep2.md §2c, verbatim off bt 427197):

    L4101  [core] funding request trimmed $1,709 -> $1,669 — satellite headroom
           will refuse the remainder; releasing core for it would only be bought back
    L4112  SATELLITE OVERFLOW: ARWR raw=+1.750 >= 1.50 — funding $1,669 of room
    L4359  [execution] FILL SELL SPY qty=2.44 price=686.74  = $1,675.74
    L4115  SKIP BUY ARWR — cash_to_use $1.69 < min $366 (allocated $854.39)
    L4582  [core] bought $1546.03 SPY @ 687.73 (band_deploy: 12.1% -> 37.6% of NAV)

$1,675.74 of core sold to fund two conviction names; $1,545.98 (92.3%) went
straight back into SPY one bar later. Measured on 4 runs / 3 windows / 2
regimes: $11,474 released, $7,104 (62%) recycled.

These tests FAIL without `core_funding_release_reserve_decisions`, and the
key's ABSENCE must leave every number byte-identical — that contract is
asserted first and last.
"""
import ast
import os
import sys
import types
from datetime import datetime

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import core_sleeve as cs  # noqa: E402
from core_sleeve import (  # noqa: E402
    CORE_DEPLOY_COST_HAIRCUT,
    core_rebalance_order,
    core_sleeve_config,
    funding_release_reserve_state,
    reset_funding_release_reserve,
)

# doc-193's shape, trimmed. `core_min_pct=0.10` is the setting bt 427197 ran.
BASE = {
    "cash_reserve_floor_pct": 0.02,
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "core_sleeve_enabled": True,
    "core_target_pct": 0.35,
    "core_min_pct": 0.10,
    "core_max_pct": 0.40,
}
# The reserve armed for ONE bar with a decision of margin. broker.py evaluates
# the core twice per bar AND the funding sell fills before the release bar's
# own cycle-end deploy, so one protected bar costs THREE refused decisions.
# `test_wiring_the_decision_ladder_is_three_per_bar` pins that against the real
# broker functions instead of trusting this note.
ON = dict(BASE, core_funding_release_reserve_decisions=4)

# bt 427197, 2026-01-05 -> 01-06, reconstructed to the dollar off the log.
NAV = 6103.0
SPY_PX = 686.74
CASH_AT_GATE = 1.69          # what `SKIP BUY ARWR` actually read
FUNDING_REQUEST = 1669.0     # after "funding request trimmed $1,709 -> $1,669"
SATELLITE = 3686.0           # NAV - cash - core, pre-release
CORE_BEFORE = NAV - CASH_AT_GATE - SATELLITE   # $2,415.31 of SPY


def setup_function(_fn):
    reset_funding_release_reserve()


def teardown_function(_fn):
    reset_funding_release_reserve()


def _release(cfg, cash=CASH_AT_GATE, core_value=CORE_BEFORE):
    return core_rebalance_order(
        cfg, nav=NAV, core_value=core_value, satellite_value=SATELLITE,
        cash=cash, funding_request=FUNDING_REQUEST)


def _deploy(cfg, cash, core_value, satellite=SATELLITE):
    return core_rebalance_order(
        cfg, nav=NAV, core_value=core_value, satellite_value=satellite,
        cash=cash)


# ── 1. the default-OFF contract ───────────────────────────────────────────

def test_key_absent_reproduces_the_427197_rebuy_exactly():
    """Without the key the leak is still there, to the dollar. This is the
    control: if this ever stops failing to reserve, the fix has changed
    behaviour with the key ABSENT and is no longer default-off."""
    cfg = core_sleeve_config(BASE)
    assert cfg.funding_release_reserve_decisions == 0
    rel = _release(cfg)
    assert rel.reason == "funding"
    released = -rel.notional
    assert round(released, 2) == round(FUNDING_REQUEST - CASH_AT_GATE, 2)
    # the release fills: cash gets the proceeds, the core shrinks by them
    dep = _deploy(cfg, cash=CASH_AT_GATE + released,
                  core_value=CORE_BEFORE - released)
    assert dep.reason == "band_deploy"
    # 92%+ of the released dollars go straight back into the index — the exact
    # shape of `[core] bought $1546.03 SPY (band_deploy: 12.1% -> 37.6%)`.
    assert dep.notional / released > 0.90
    assert round(dep.current_weight, 3) == 0.123   # log says 12.1%
    assert round(dep.target_weight, 3) == 0.376    # log says 37.6%
    # and nothing was written to the ledger
    assert funding_release_reserve_state() == {"usd": 0.0, "decisions": 0}


def test_key_absent_leaves_every_other_reason_untouched():
    cfg = core_sleeve_config(BASE)
    for kw, expect in (
        (dict(nav=6000.0, core_value=2100.0, satellite_value=3900.0, cash=0.0,
              days_since_rebalance=99), "within_band"),
        (dict(nav=6000.0, core_value=0.0, satellite_value=2280.0, cash=3720.0,
              days_since_rebalance=2), "cadence_hold"),
        (dict(nav=6000.0, core_value=2040.0, satellite_value=3000.0,
              cash=140.0, days_since_rebalance=99), "deploy_below_min"),
    ):
        assert core_rebalance_order(cfg, **kw).reason == expect
    assert funding_release_reserve_state()["usd"] == 0.0


def test_key_absent_is_byte_identical_across_a_randomised_state_sweep():
    """The default-off contract, swept rather than sampled.

    Five levers shipped inert this session; the mirror-image risk is a lever
    that ships LIVE on a config that never asked for it. With the key absent
    the deploy must still be exactly `min(drift, spendable)`, the new reason
    must never appear, and the ledger must stay empty — no state is written at
    all, so there is nothing to leak into the next bar or the next run.
    """
    import random
    rng = random.Random(7)
    cfg = core_sleeve_config(BASE)
    for _ in range(4000):
        nav = rng.choice([0.0, 100.0, 6000.0, 6103.0, 250000.0])
        hi = max(nav, 1.0)
        kw = dict(
            nav=nav,
            core_value=rng.uniform(0, hi),
            satellite_value=rng.uniform(0, hi),
            cash=rng.uniform(0, hi),
            regime=rng.choice(["", "bull", "chop", "bear", "crash"]),
            bear_dwell_days=rng.choice([0, 1, 3, 9]),
            days_since_rebalance=rng.choice([None, 0, 2, 5, 99]),
            funding_request=rng.choice([0.0, rng.uniform(0, 3000)]),
            circuit_tier=rng.choice(["", "soft", "hard", "kill"]),
            turnover_exhausted=rng.choice([True, False]),
        )
        order = core_rebalance_order(cfg, **kw)
        assert order.reason != "funding_release_reserved"
        if order.reason == "band_deploy":
            _sp = max(0.0, kw["cash"] - cfg.cash_floor_pct * nav)
            _sp /= (1.0 + CORE_DEPLOY_COST_HAIRCUT)
            _drift = (order.target_weight - order.current_weight) * nav
            assert abs(order.notional - min(_drift, _sp)) < 1e-9
        assert funding_release_reserve_state() == {"usd": 0.0, "decisions": 0}


def test_garbage_key_values_never_raise_and_stay_off():
    for bad in ("", None, "abc", -3, 0):
        cfg = core_sleeve_config(
            dict(BASE, core_funding_release_reserve_decisions=bad))
        assert cfg.funding_release_reserve_decisions == 0
        _release(cfg)
        assert funding_release_reserve_state()["usd"] == 0.0


# ── 2. the fix ────────────────────────────────────────────────────────────

def test_a_funding_release_is_reserved_and_band_deploy_may_not_take_it():
    """THE FIX. Same bar sequence, same dollars, key ON."""
    cfg = core_sleeve_config(ON)
    rel = _release(cfg)
    assert rel.reason == "funding"
    released = -rel.notional
    assert round(funding_release_reserve_state()["usd"], 2) == round(released, 2)
    dep = _deploy(cfg, cash=CASH_AT_GATE + released,
                  core_value=CORE_BEFORE - released)
    assert dep.reason == "funding_release_reserved"
    assert dep.notional == 0.0
    # the drift it wanted to close is unchanged — this is a refusal, not a
    # re-measurement
    assert round(dep.target_weight - dep.current_weight, 3) == 0.253


def test_a_bar_the_core_would_not_have_deployed_on_burns_nothing():
    """The credit is a budget for REFUSING re-buys. A bar with no cash to
    re-buy with was not a re-buy, and must not shorten the credit — otherwise
    a few quiet bars silently disarm the fix. (This is how a lever ships
    inert.)"""
    cfg = core_sleeve_config(ON)
    rel = _release(cfg)
    released = -rel.notional
    dry = _deploy(cfg, cash=100.0, core_value=CORE_BEFORE - released)
    assert dry.reason == "deploy_below_min"
    assert funding_release_reserve_state()["decisions"] == 4  # untouched


def test_the_credit_expires_so_declined_cash_still_comes_home():
    """A reserve that never expires is a core that can never re-deploy. After
    the satellite has had its bar and declined, the index takes the cash."""
    cfg = core_sleeve_config(ON)
    rel = _release(cfg)
    released = -rel.notional
    kw = dict(cash=CASH_AT_GATE + released, core_value=CORE_BEFORE - released)
    for left in (3, 2, 1, 0):
        assert _deploy(cfg, **kw).reason == "funding_release_reserved"
        assert funding_release_reserve_state()["decisions"] == left
    back = _deploy(cfg, **kw)
    assert back.reason == "band_deploy" and back.notional > 0.0


def test_a_satellite_that_actually_spends_the_credit_clears_it_for_free():
    """The good case needs no bookkeeping: once ARWR is bought the cash is
    gone, the claim collapses to $0 and the core is not blocked at all."""
    cfg = core_sleeve_config(ON)
    rel = _release(cfg)
    released = -rel.notional
    # satellite spent it: cash back to the floor, satellite up by the clip
    dep = _deploy(cfg, cash=CASH_AT_GATE, core_value=CORE_BEFORE - released,
                  satellite=SATELLITE + released)
    assert dep.reason != "funding_release_reserved"
    assert funding_release_reserve_state()["decisions"] == 4  # nothing burned


def test_a_partial_reserve_still_lets_the_core_deploy_the_rest():
    """The reserve withholds the released dollars, not the whole balance."""
    cfg = core_sleeve_config(ON)
    rel = _release(cfg)
    released = -rel.notional
    # $900 of unrelated cash arrives on top of the credit
    dep = _deploy(cfg, cash=CASH_AT_GATE + released + 900.0,
                  core_value=CORE_BEFORE - released)
    assert dep.reason == "band_deploy"
    _spendable = (CASH_AT_GATE + released + 900.0 - 0.02 * NAV)
    _spendable /= (1.0 + CORE_DEPLOY_COST_HAIRCUT)
    assert round(dep.notional, 2) == round(max(0.0, _spendable - released), 2)
    assert funding_release_reserve_state()["decisions"] == 3  # it did bite


def test_a_new_release_refreshes_the_budget_and_adds_to_the_credit():
    cfg = core_sleeve_config(ON)
    first = -_release(cfg).notional
    kw = dict(cash=CASH_AT_GATE + first, core_value=CORE_BEFORE - first)
    _deploy(cfg, **kw)
    assert funding_release_reserve_state()["decisions"] == 3
    second = -core_rebalance_order(
        cfg, nav=NAV, core_value=CORE_BEFORE - first,
        satellite_value=SATELLITE, cash=0.0, funding_request=500.0).notional
    st = funding_release_reserve_state()
    assert st["decisions"] == 4
    assert round(st["usd"], 2) == round(first + second, 2)


def test_the_credit_never_blocks_a_sell_or_the_bear_derisk():
    """A capital-preservation path must never be gated by a buy-side reserve —
    the 2026-08-03 sweep's MED-HIGH finding, in a new dress."""
    cfg = core_sleeve_config(ON)
    _release(cfg)
    cut = core_rebalance_order(
        cfg, nav=NAV, core_value=NAV * 0.9, satellite_value=0.0, cash=100.0,
        regime="bear", bear_dwell_days=9, days_since_rebalance=0)
    assert cut.notional < 0.0
    band_cut = core_rebalance_order(
        cfg, nav=NAV, core_value=NAV * 0.9, satellite_value=NAV * 0.05,
        cash=NAV * 0.05, days_since_rebalance=99)
    assert band_cut.reason == "band_release" and band_cut.notional < 0.0


def test_reserve_is_bounded_by_nav_and_by_cash():
    cfg = core_sleeve_config(ON)
    for _ in range(20):
        core_rebalance_order(cfg, nav=NAV, core_value=NAV * 0.5,
                             satellite_value=0.0, cash=0.0,
                             funding_request=NAV)
    assert funding_release_reserve_state()["usd"] <= NAV + 1e-9


# ── 3. the same thing through broker.py's real call path ──────────────────
#
# `_residual_sleeve_release` (cycle start) and `_residual_sleeve_deploy`
# (cycle end) both call `core_rebalance_order`; the release path DISCARDS a
# positive notional. That is why the credit's unit is a refused DECISION and
# why one bar costs two of them. AST-extracted because broker.py argparses at
# module scope (same pattern as test_core_sleeve_wiring.py).

_WANTED = {"_residual_sleeve_config", "_chop_ret20_cfg",
           "_residual_sleeve_release", "_residual_sleeve_deploy",
           "_submit_portfolio_signal", "_signal_result_is_confirmed",
           "_conviction_bear_alloc"}
_CORE_PREFIXES = ("_core_sleeve", "_core_turnover", "_turnover_ledger",
                  "_turnover_is_governed", "_CORE_")
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
for _name in _WANTED | _WANTED_CONSTS | {"_core_sleeve_decide", "_core_sleeve_cfg"}:
    assert _name in _ns, f"failed to extract {_name} from broker.py"
b = types.SimpleNamespace(**{k: v for k, v in _ns.items() if not k.startswith("__")})
b._ns = _ns

NOW = datetime(2026, 1, 5, 15)
_SLEEVE = {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
}


def _spec(**over):
    cfg = dict(_SLEEVE, **BASE)
    cfg.update(over)
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


class _Book:
    """The 427197 book on 2026-01-05, with next-event execution: a SELL raises
    cash and lowers the position, which is what makes the released dollars
    visible to the cycle-end deploy."""

    def __init__(self):
        self.cash = CASH_AT_GATE
        self.spy = CORE_BEFORE / SPY_PX
        self.buys = []
        self.sells = []

    # -- emulator surface used by the sleeve ---------------------------------
    def get_cash(self):
        return self.cash

    def get_portfolio_value(self, prices=None):
        return NAV

    def get_positions(self):
        return {"SPY": self.spy}

    def execute_signal(self, sym, sig, px, timestamp=None, sell_fraction=None,
                       cash_per_trade=None):
        if sig < 0:
            qty = self.spy * float(sell_fraction or 0.0)
            self.spy -= qty
            self.cash += qty * px
            self.sells.append(qty * px)
        else:
            self.cash -= float(cash_per_trade or 0.0)
            self.spy += float(cash_per_trade or 0.0) / px
            self.buys.append(float(cash_per_trade or 0.0))
        return True


def _run_one_bar(spec, book):
    """One broker bar: release (cycle start, carrying the funding request),
    then deploy (cycle end)."""
    b._residual_sleeve_release(book, {"SPY": SPY_PX}, NOW, spec, None,
                               FUNDING_REQUEST)
    b._residual_sleeve_deploy(book, {"SPY": SPY_PX}, NOW, spec, None)


def _run_next_bar(spec, book):
    """The NEXT bar: the satellite buy was refused (`SKIP BUY ARWR —
    cash_to_use $1.69`), so no funding request, and the released cash is
    sitting there."""
    b._residual_sleeve_release(book, {"SPY": SPY_PX}, NOW, spec, None, 0.0)
    b._residual_sleeve_deploy(book, {"SPY": SPY_PX}, NOW, spec, None)


def _reset_wiring():
    b._ns["_RESIDUAL_SLEEVE_STATE"].clear()
    b._ns["_strategy_cache"] = {}
    reset_funding_release_reserve()


def test_wiring_without_the_key_the_core_buys_its_own_release_back():
    """The measured leak, end to end through broker.py's own functions."""
    _reset_wiring()
    book, spec = _Book(), _spec()
    _run_one_bar(spec, book)
    _run_next_bar(spec, book)
    released, rebought = sum(book.sells), sum(book.buys)
    assert released > 1600.0
    assert rebought / released > 0.90, (released, rebought)


def test_wiring_with_the_key_the_release_survives_the_bar():
    """Same book, same bars, key ON: zero dollars recycled on the bar the
    satellite was supposed to get."""
    _reset_wiring()
    book, spec = _Book(), _spec(core_funding_release_reserve_decisions=4)
    _run_one_bar(spec, book)
    _run_next_bar(spec, book)
    assert sum(book.sells) > 1600.0
    assert book.buys == []


def test_wiring_the_decision_ladder_is_three_per_bar():
    """WHY THE UNIT IS A DECISION, pinned against broker.py's own functions.

    bar N   deploy   -> refused #1  (the sell filled before cycle end)
    bar N+1 release  -> refused #2  (positive notional, discarded by broker)
    bar N+1 deploy   -> refused #3  <- the re-buy sweep2 measured
    A key set below 3 would let the re-buy through and ship INERT."""
    _reset_wiring()
    book, spec = _Book(), _spec(core_funding_release_reserve_decisions=4)
    _run_one_bar(spec, book)
    assert funding_release_reserve_state()["decisions"] == 3
    _run_next_bar(spec, book)
    assert funding_release_reserve_state()["decisions"] == 1
    assert book.buys == []

    _reset_wiring()
    book2, spec2 = _Book(), _spec(core_funding_release_reserve_decisions=2)
    _run_one_bar(spec2, book2)
    _run_next_bar(spec2, book2)
    assert book2.buys, "a 2-decision credit expires inside the bar it protects"


def test_wiring_the_credit_expires_and_the_index_gets_the_cash_back():
    """The satellite had its bar and did not take it, so the index is allowed
    to hold the cash again. The reserve DELAYS, it does not confiscate — a
    permanent block would be a core that can never re-deploy."""
    _reset_wiring()
    book, spec = _Book(), _spec(core_funding_release_reserve_decisions=4)
    _run_one_bar(spec, book)
    _run_next_bar(spec, book)
    assert book.buys == []
    _run_next_bar(spec, book)
    assert book.buys and sum(book.buys) > 1000.0


def test_wiring_a_satellite_that_spends_it_is_never_blocked():
    """The good case, end to end: ARWR actually fills on the next bar, so the
    credit collapses on its own and the core is not held out at all."""
    _reset_wiring()
    book, spec = _Book(), _spec(core_funding_release_reserve_decisions=4)
    b._residual_sleeve_release(book, {"SPY": SPY_PX}, NOW, spec, None,
                               FUNDING_REQUEST)
    spent = book.cash - CASH_AT_GATE          # the satellite buys the name
    book.cash -= spent
    b._residual_sleeve_deploy(book, {"SPY": SPY_PX}, NOW, spec, None)
    assert book.buys == []                    # no cash, nothing to re-buy
    assert funding_release_reserve_state()["decisions"] == 4   # nothing burned
