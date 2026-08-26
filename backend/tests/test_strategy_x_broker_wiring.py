"""strategy_x's broker-side wiring: universe declaration and price prep.

The failure this guards is the one this repo hits most — a strategy that has
BARS for a symbol but no PRICE skips it silently, returns {}, and therefore
never publishes the discovery channel that would have fixed it. broker.py is not
import-safe (argparse at module scope SystemExits under pytest), so the helpers
are AST-extracted, the pattern used by test_residual_sleeve.py.
"""
import ast
import os
import sys
import types

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_WANTED = {"_strategy_x_specs", "_strategy_x_universe_symbols", "_strategy_x_prepare"}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
_ns = {}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module([_node], []), "broker.py", "exec"), _ns)

universe = _ns["_strategy_x_universe_symbols"]
prepare = _ns["_strategy_x_prepare"]


def spec(**cfg):
    base = {"strategy_x_enabled": True}
    base.update(cfg)
    return {"strategy": "strategy_x", "weight": 1.0, "config": base}


def test_enabled_spec_declares_the_core_legs():
    assert universe([spec()])[:3] == ["QQQ", "TQQQ", "SPY"]


def test_a_config_relying_on_DEFAULTS_still_declares_symbols():
    """The broker list and the strategy list must be the SAME list. Computing
    it twice is how they drift: a config that omits the core symbols would
    fetch zero bars here while the strategy looked up QQQ/TQQQ/SPY."""
    from strategy_x import DEFAULTS, strategy_x_universe

    got = universe([{"strategy": "strategy_x", "weight": 1.0,
                     "config": {"strategy_x_enabled": True}}])
    assert got == strategy_x_universe({**DEFAULTS, "strategy_x_enabled": True})
    assert "QQQ" in got and "TQQQ" in got


def test_pascalcase_strategy_name_is_matched():
    """`_strategy_name_to_module_and_class` accepts both spellings, so a
    PascalCase document runs the strategy — its bars must be fetched too."""
    assert universe([{"strategy": "StrategyX", "weight": 1.0,
                      "config": {"strategy_x_enabled": True}}])


def test_settings_in_conditions_are_honoured():
    """The dispatcher merges conditions and config; so must this."""
    got = universe([{"strategy": "strategy_x", "weight": 1.0,
                     "conditions": {"strategy_x_enabled": True,
                                    "core_bull_symbol": "SOXL"},
                     "config": {}}])
    assert "SOXL" in got


def test_disabled_absent_or_zero_weight_declares_nothing():
    assert universe([spec()] and [{"strategy": "strategy_x", "weight": 1.0,
                                   "config": {"strategy_x_enabled": False}}]) == []
    assert universe([{"strategy": "graph_nexus_analysis", "weight": 1.0,
                      "config": {}}]) == []
    # weight <= 0 is skipped by run_run_once_strategies, so fetching for it
    # would be pure cost.
    assert universe([{"strategy": "strategy_x", "weight": 0,
                      "config": {"strategy_x_enabled": True}}]) == []
    assert universe(None) == []
    assert universe([None, "junk", 7]) == []


def test_commodity_symbols_only_when_the_sleeve_is_funded():
    off = universe([spec(commodity_pct=0.0, commodity_symbols=["GLD"])])
    on = universe([spec(commodity_pct=0.2, commodity_symbols=["GLD"])])
    assert "GLD" not in off
    assert "GLD" in on


def test_symbols_are_normalised_the_same_way_the_strategy_normalises_them():
    """A padded value that fetches under one spelling and is looked up under
    another is what made the sleeve's exemption set match nothing."""
    got = universe([spec(core_bull_symbol="  tqqq  ")])
    assert "TQQQ" in got
    assert "  tqqq  " not in got


def test_bear_symbol_is_declared_so_the_leg_can_be_priced():
    assert "SQQQ" in universe([spec(core_bear_symbol="SQQQ")])


@pytest.mark.parametrize("mode", ["shadow", "active"])
def test_bear_system_modes_discover_all_overlay_symbols(mode):
    """Every overlay input must be available before run_once makes a decision."""
    assert universe([spec(bear_system_mode=mode)]) == [
        "QQQ", "TQQQ", "SPY", "BIL", "DBMF", "KMLM", "CTA", "SQQQ",
    ]


def test_bear_system_off_does_not_discover_overlay_symbols_unless_core_uses_one():
    """The default must have no feed-cost side effect, apart from core legs."""
    off = universe([spec(bear_system_mode="off")])
    assert not set(off).intersection({"BIL", "DBMF", "KMLM", "CTA", "SQQQ"})

    core_sqqq = universe([spec(bear_system_mode="off", core_bear_symbol="SQQQ")])
    assert core_sqqq == ["QQQ", "TQQQ", "SPY", "SQQQ"]


def _set_prepare_environment(*, run_mode, visible_prices, fetched):
    """Provide only the broker dependencies `_strategy_x_prepare` actually uses."""
    _ns["MODE_BACKTEST"] = "backtest"
    _ns["MODE_LIVE"] = "live"
    _ns["mode"] = run_mode
    _ns["_log"] = lambda *_args, **_kwargs: None
    _ns["_ensure_backtest_history_for_symbols"] = (
        lambda _data, symbols, **_kwargs: fetched.extend(symbols))
    _ns["_get_prices_at_time"] = lambda _data, symbols, _time: {
        symbol: visible_prices[symbol] for symbol in symbols
        if symbol in visible_prices
    }


def test_prepare_prices_each_active_bear_input_from_visible_backtest_bars():
    """An empty mark map may not make an otherwise visible overlay silently inert."""
    extras = {"BIL": 91.0, "DBMF": 31.0, "KMLM": 28.0, "CTA": 22.0, "SQQQ": 17.0}
    visible = {"QQQ": 500.0, "TQQQ": 70.0, "SPY": 610.0, **extras}
    fetched, prices = [], {}
    _set_prepare_environment(run_mode="backtest", visible_prices=visible, fetched=fetched)

    prepare({symbol: [{"c": price}] for symbol, price in visible.items()}, prices,
            object(), [spec(bear_system_mode="active")])

    assert {symbol: prices[symbol] for symbol in extras} == extras
    assert fetched == []


def test_prepare_off_mode_neither_fetches_nor_prices_overlay_symbols():
    """Default-off means the broker does not look up or mark the extra five."""
    fetched, prices = [], {}
    _set_prepare_environment(
        run_mode="backtest",
        visible_prices={"QQQ": 500.0, "TQQQ": 70.0, "SPY": 610.0},
        fetched=fetched,
    )

    prepare({"QQQ": [{"c": 500.0}], "TQQQ": [{"c": 70.0}], "SPY": [{"c": 610.0}]},
            prices, object(), [spec(bear_system_mode="off")])

    assert not set(prices).intersection({"BIL", "DBMF", "KMLM", "CTA", "SQQQ"})
    assert fetched == []


def test_no_duplicates_across_multiple_specs():
    got = universe([spec(), spec()])
    assert len(got) == len(set(got))
