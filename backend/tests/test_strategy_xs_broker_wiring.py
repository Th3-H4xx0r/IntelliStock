"""Strategy XS must get bars and prices for legs the watchlist never lists.

There are TWO wiring points and missing either one is silent. The fetch site
decides which symbols get BARS downloaded; the prepare site decides which get
a PRICE on the bar. Without bars the filter is blind; without a price
`targets_to_orders` skips the leg. Either way the strategy just emits nothing.
"""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_BROKER = os.path.join(_BACKEND, "broker.py")


def _extract(*names):
    """AST-extract broker functions into a stub namespace.

    broker.py argparses at module scope and SystemExits under pytest, so it
    cannot be imported. This is the same technique the Strategy X broker tests
    use.
    """
    tree = ast.parse(open(_BROKER).read())
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in names]
    assert wanted, f"none of {names} found in broker.py"
    ns = {"mode": "backtest", "MODE_BACKTEST": "backtest",
          "MODE_LIVE": "live", "data_feed": None,
          "_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def spec(**config):
    return [{"strategy": "strategy_xs", "config": config}]


def test_the_declared_xs_universe_is_fetched():
    ns = _extract("_strategy_xs_universe_symbols")
    syms = ns["_strategy_xs_universe_symbols"](
        spec(strategy_xs_enabled=True, diversifier_pct=0.45))
    assert set(syms) >= {"QQQ", "TQQQ", "BIL", "GLD", "UUP", "DBMF"}


def test_a_disabled_xs_contributes_no_symbols():
    ns = _extract("_strategy_xs_universe_symbols")
    assert ns["_strategy_xs_universe_symbols"](
        spec(strategy_xs_enabled=False)) == []


def test_an_absent_xs_contributes_no_symbols():
    ns = _extract("_strategy_xs_universe_symbols")
    assert ns["_strategy_xs_universe_symbols"](
        [{"strategy": "graph_nexus_analysis", "config": {}}]) == []


def test_a_malformed_spec_list_does_not_raise():
    ns = _extract("_strategy_xs_universe_symbols")
    for junk in (None, [], [None], ["strategy_xs"], [{"strategy": None}]):
        assert ns["_strategy_xs_universe_symbols"](junk) == [], junk


def test_both_wiring_points_reference_the_xs_universe():
    """A source assertion, because the fetch site is inline in a 4,000-line
    function and cannot be AST-extracted. Missing either point is silent, so
    the wiring itself has to be asserted somewhere."""
    source = open(_BROKER).read()
    uses = source.count("_strategy_xs_universe_symbols(")
    # one definition + the prepare site + the fetch site
    assert uses >= 3, f"expected the XS universe at both wiring points, saw {uses}"
