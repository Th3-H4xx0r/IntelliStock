"""ADVERSARIAL sweep of the Layer-3 index-core allocation. NOT FOR COMMIT.

Every test in here is written to FAIL against the current implementation and
documents a defect. Same AST-extraction harness as test_core_sleeve_wiring.py
because broker.py is not import-safe.
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
b = types.SimpleNamespace(**{k: v for k, v in _ns.items() if not k.startswith("__")})
b._ns = _ns

from core_sleeve import core_rebalance_order, core_sleeve_config  # noqa: E402

LEGACY_CFG = {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
    "cash_reserve_floor_pct": 0.02,
}
NOW = datetime(2026, 4, 6, 15)


def _spec(**over):
    cfg = dict(LEGACY_CFG, core_sleeve_enabled=True)
    cfg.update(over)
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


CORE = _spec()


class _Book:
    """A book that actually MOVES when a signal is submitted, so a whole bar
    (release -> blocked buy -> deploy) can be replayed. NAV carries a held name
    at its last known price, matching PortfolioEmulator.get_portfolio_value."""

    def __init__(self, cash, positions=None, marks=None):
        self._cash = float(cash)
        self._positions = dict(positions or {})
        self._last = dict(marks or {})
        self.signals = []

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = dict(self._last)
        px.update({k: v for k, v in (prices or {}).items() if v})
        return self._cash + sum(
            q * float(px.get(t, 0.0) or 0.0) for t, q in self._positions.items())

    def execute_signal(self, ticker, signal, price, timestamp=None,
                       cash_per_trade=1000.0, sell_fraction=1.0, **kw):
        price = float(price)
        if signal == 1:
            notional = float(cash_per_trade or 0.0)
            self._positions[ticker] = self._positions.get(ticker, 0.0) + notional / price
            self._cash -= notional
            self.signals.append(("buy", ticker, notional))
        else:
            qty = self._positions.get(ticker, 0.0) * float(sell_fraction or 0.0)
            self._positions[ticker] = self._positions.get(ticker, 0.0) - qty
            self._cash += qty * price
            self.signals.append(("sell", ticker, qty * price))
        self._last[ticker] = price
        return True


def setup_function(_fn):
    b._ns["_RESIDUAL_SLEEVE_STATE"].clear()
    b._ns["_strategy_cache"] = {}
    b._ns["_sleeve_circuit_tier"] = lambda: ""
    b._ns["_sleeve_market_regime"] = lambda: "bull"


def _regime(r):
    b._ns["_sleeve_market_regime"] = lambda: r


def _dwell(d):
    b._ns["_strategy_cache"] = {"graph_nexus_analysis": {"_bear_dwell_bars": int(d)}}


# ── A1 the cap is armed while the core itself refuses to arm ────────────────

def test_A1_cap_is_live_even_when_the_core_refused_to_arm():
    """core_sleeve_enabled=True + residual_sleeve_enabled=False.

    `_core_sleeve_cfg` FAILS CLOSED (no core, ever). But the standing satellite
    cap reads the RAW config and only checks core_sleeve_enabled, so it arms.
    Result: no core is ever bought, the legacy sleeve is off too, and every new
    satellite name is refused above 38% of NAV. The book is permanently capped
    at 38% invested with 62% dead cash and no mechanism to deploy it.
    """
    spec = _spec(residual_sleeve_enabled=False)
    assert b._core_sleeve_cfg(spec) is None, "core must refuse to arm"
    book = _Book(6000.0, {"AAA": 100.0}, {"AAA": 40.0})   # satellite 4000/10000
    assert b._core_sleeve_block_new_satellite(
        book, {"AAA": 40.0}, spec, "BBB") is False, (
        "the satellite cap must be inert whenever the core is inert")


# ── A2 an unpriced holding silently disables the cap ────────────────────────

def test_A2_an_unpriced_holding_undercounts_the_satellite():
    """`_core_sleeve_decide` sizes the satellite as a NAV RESIDUAL precisely so
    an unpriced holding cannot vanish. The cap sums over individually priced
    positions instead — the exact arithmetic the design forbids. A held name
    with no bar this tick contributes $0 of satellite while still contributing
    its full value to NAV, so the ratio is understated and the cap fails open.
    """
    # NAV 10,000: cash 1,000 + SPY 1,000 + AAA 3,000 + BBB 5,000.
    # True satellite = 8,000/10,000 = 80%, far past the 38% share, but the
    # 50%-of-NAV name is the one with no bar this tick.
    book = _Book(1000.0, {"SPY": 1.6667, "AAA": 75.0, "BBB": 125.0},
                 {"SPY": 600.0, "AAA": 40.0, "BBB": 40.0})
    prices = {"SPY": 600.0, "AAA": 40.0}          # BBB has no bar this tick
    assert abs(book.get_portfolio_value(prices) - 10000.0) < 1.0
    assert b._core_sleeve_block_new_satellite(book, prices, CORE, "CCC") is True


# ── A3 the held-name exemption is the whole overrun ─────────────────────────

def test_A3_winner_adds_walk_the_satellite_past_the_cap_unbounded():
    """The overrun bt 589998 describes is 'the satellite kept buying across
    bars'. Winner-adds (`winner_add_buy`) are buys into names ALREADY HELD, and
    those are exactly what the cap exempts. The regime cap it is modelled on
    counts POSITIONS, which an add cannot grow; a WEIGHT cap is defeated by one.
    """
    book = _Book(0.0, {"AAA": 100.0, "BBB": 100.0, "SPY": 1.0},
                 {"AAA": 40.0, "BBB": 40.0, "SPY": 600.0})   # satellite 80%
    prices = {"AAA": 40.0, "BBB": 40.0, "SPY": 600.0}
    assert b._core_sleeve_block_new_satellite(book, prices, CORE, "AAA") is True


# ── A4 the funding release / blocked buy round trip ─────────────────────────

def test_A4_a_blocked_satellite_buy_round_trips_the_core_in_one_bar():
    """The three mechanisms fighting.

    `_core_funding_request` is summed from `nexus_executable_buys` BEFORE the
    execution pass (broker.py:13167) and the core releases that shortfall
    immediately, bypassing the cadence and NOT stamping the cadence clock. The
    satellite cap then refuses those very buys later in the same bar
    (broker.py:13534). Nothing tells the release the buy died, so at cycle end
    the deploy sees the core below band and buys it straight back.

    Net allocation change: zero. Turnover: 2x the funding request, on the one
    lane that is EXEMPT from the turnover budget.
    """
    prices = {"SPY": 600.0, "AAA": 40.0}
    book = _Book(200.0, {"SPY": 10.0, "AAA": 95.0}, prices)   # 6000/3800/200
    assert abs(book.get_portfolio_value(prices) - 10000.0) < 1e-6
    # the satellite is at exactly its 38% design share, so every NEW name is
    # refused this bar and for every bar after it
    assert b._core_sleeve_block_new_satellite(book, prices, CORE, "NEW") is True

    b._residual_sleeve_release(book, prices, NOW, CORE, None, 1500.0)
    # ... the approved buy is now blocked by the cap: nothing is bought ...
    b._residual_sleeve_deploy(book, prices, NOW, CORE)

    churn = sum(n for _side, _sym, n in book.signals)
    assert book.signals == [], (
        f"the core round-tripped ${churn:,.0f} of notional for a buy that was "
        f"blocked: {book.signals}")


# ── A5 core exemption relocates the starvation onto the satellite ───────────

def test_A5_establishing_the_core_exhausts_the_whole_monthly_budget():
    """Bug #2 was 'the budget starved the core'. Exempting the core did not
    remove the arithmetic — it moved the victim. Core notional is still BOOKED
    into the ledger, and establishing a 98% core costs 98% of NAV in one-way
    turnover, so a 50%/month budget is exhausted by the core's first buy and
    every DISCRETIONARY buy is blocked for the rest of the window.

    Worse, it is self-reinforcing: with the satellite lane blocked the
    satellite weight decays toward 0, which raises the core target, which
    causes more core buying, which keeps the budget exhausted.
    """
    spec = _spec(turnover_budget_monthly_pct=0.50)
    book = _Book(10_000.0, {}, {"SPY": 600.0})
    b._residual_sleeve_deploy(book, {"SPY": 600.0}, NOW, spec)
    assert book.signals, "the core should have been established"
    blocked, used = b._core_turnover_state(spec, 10_000.0, NOW)
    assert blocked is False, (
        f"the core's own establishment burnt {used:.0%} of a 50% budget and "
        "locked the satellite lane out for 21 sessions")


# ── A6 the bear de-risk is a bigger one-bar liquidation than it replaced ────

def test_A6_bear_derisk_from_the_default_state_sells_68pp_in_one_bar():
    """The design's stated purpose: 'today's release zeroes the leg outright,
    which is 60pp of one-way notional in a single bar'. But the DEFAULT state
    of this design is a 98% core (silent graph -> satellite 0 -> core = max_pct),
    and core_bear_scale drives the target to 30%. That is a 68pp one-bar sale —
    LARGER than the event the bounded de-risk exists to prevent.
    """
    _regime("bear")
    _dwell(5)
    prices = {"SPY": 600.0}
    book = _Book(200.0, {"SPY": 16.3333}, prices)          # 9,800 core on 10,000
    b._residual_sleeve_release(book, prices, NOW, CORE)
    sold = sum(n for side, _s, n in book.signals if side == "sell")
    assert sold / 10_000.0 <= 0.60, (
        f"one-bar bear de-risk sold {sold / 10_000.0:.0%} of NAV")


# ── A7 a sub-$5 funding shortfall suppresses the bear de-risk ───────────────

def test_A7_a_one_dollar_funding_shortfall_cancels_the_bear_derisk():
    """`core_rebalance_order` evaluates funding FIRST and returns
    'funding_below_min' for a shortfall under $5 — before the bear branch is
    ever reached. Any bar on which the allocator is $0.01-$4.99 short therefore
    silently cancels the whole risk-reducing path.
    """
    cfg = core_sleeve_config(dict(LEGACY_CFG, core_sleeve_enabled=True))
    order = core_rebalance_order(
        cfg, nav=10_000.0, core_value=9_800.0, satellite_value=0.0, cash=100.0,
        regime="bear", bear_dwell_days=9, days_since_rebalance=None,
        funding_request=101.0)
    assert order.reason == "bear_derisk", (
        f"a $1 funding shortfall returned {order.reason!r} and skipped the "
        "bear de-risk entirely")


# ── A8 the cash floor is not honoured once the residual clamps ──────────────

def test_A8_a_clamped_core_spends_the_entire_cash_floor():
    """`core = clamp(1 - cash_floor - satellite, min, max)` only reserves the
    cash floor while the expression is INSIDE the clamp. Once the satellite
    pushes the raw residual below min_pct the clamp lifts the target back up,
    and `buy = min(drift_usd, cash)` then sweeps 100% of cash into the index —
    the 2% floor the same config declares is simply gone.
    """
    cfg = core_sleeve_config(dict(LEGACY_CFG, core_sleeve_enabled=True))
    order = core_rebalance_order(
        cfg, nav=10_000.0, core_value=0.0, satellite_value=9_000.0, cash=1_000.0,
        regime="bull", days_since_rebalance=None)
    assert order.notional <= 1_000.0 - 0.02 * 10_000.0, (
        f"the core bought ${order.notional:,.0f} of a ${1000:,.0f} cash "
        "balance, leaving nothing for the declared 2% cash floor")


# ── A9 a padded sleeve symbol turns the core into satellite ─────────────────

def test_A9_a_padded_sleeve_symbol_makes_the_cap_count_the_core():
    """`_residual_sleeve_config` does `.strip().upper()` with an explicit
    comment that a padded value has broken the sleeve before. The cap does
    `.upper()` only, so ' SPY ' never matches the held key 'SPY' and the core
    itself is counted as satellite — every new name refused, forever.
    """
    spec = _spec(residual_sleeve_symbol=" SPY ")
    book = _Book(0.0, {"SPY": 10.0, "AAA": 25.0}, {"SPY": 600.0, "AAA": 40.0})
    prices = {"SPY": 600.0, "AAA": 40.0}          # core 6000, satellite 1000
    assert b._core_sleeve_block_new_satellite(book, prices, spec, "BBB") is False


# ── A10 a high core target silently switches the stock picker off ───────────

def test_A10_a_high_core_target_collapses_the_satellite_share_to_5pct():
    """share = max(0.05, 1 - core_target_pct - cash_floor). An operator who
    raises core_target_pct to 0.95 does not get 'a bigger core' — they get a 3%
    satellite budget floored at 5%, i.e. the graph is switched off with no log
    and no config error.
    """
    spec = _spec(core_target_pct=0.95)
    book = _Book(0.0, {"SPY": 15.0, "AAA": 25.0}, {"SPY": 600.0, "AAA": 40.0})
    prices = {"SPY": 600.0, "AAA": 40.0}          # satellite 1000/10000 = 10%
    assert b._core_sleeve_block_new_satellite(book, prices, spec, "BBB") is False


# ── A11 nothing ever brings an overrun satellite back ───────────────────────

def test_A11_an_appreciation_overrun_never_recovers():
    """The cap is entry-only. If the satellite appreciates past its share
    nothing sells it, so the core's residual target stays depressed and the
    core can never re-reach 60% — the state bt 589998 was reported for is a
    STABLE state, not a transient one.
    """
    cfg = core_sleeve_config(dict(LEGACY_CFG, core_sleeve_enabled=True))
    # satellite appreciated to 68% of NAV; nothing bought it there, and nothing
    # will sell it back.
    order = core_rebalance_order(
        cfg, nav=10_000.0, core_value=3_000.0, satellite_value=6_800.0,
        cash=200.0, regime="bull", days_since_rebalance=None)
    assert order.target_weight >= 0.55, (
        f"core target pinned at {order.target_weight:.0%} with no mechanism to "
        "reduce the satellite")
