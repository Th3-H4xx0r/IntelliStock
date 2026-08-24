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

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_WANTED = {"_strategy_x_specs", "_strategy_x_universe_symbols"}
_src = open(os.path.join(_backend, "broker.py")).read()
_tree = ast.parse(_src)
_ns = {}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module([_node], []), "broker.py", "exec"), _ns)

universe = _ns["_strategy_x_universe_symbols"]


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


def test_no_duplicates_across_multiple_specs():
    got = universe([spec(), spec()])
    assert len(got) == len(set(got))
