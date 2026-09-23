"""Broker ownership guard between Strategy X and the residual bear sleeve."""
from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest

from strategy_x_bear import BearSystemStateError


BROKER_PATH = Path(__file__).resolve().parents[1] / "broker.py"


#: Everything `run_run_once_strategies` reads at module scope, extracted with
#: it. NOT optional: the dispatcher wraps each strategy in a blanket
#: `except Exception` that logs RED and continues, so a name missing here is a
#: NameError nobody sees — every test in this file then fails with an empty
#: result and none of them says why. `_eb_live_portfolio_view` (cb9d2ba) cost
#: this file 15 failures exactly that way. `test_every_name_the_dispatcher_
#: reads_is_in_the_harness` is the sentinel that now catches the next one.
_DISPATCHER_NAMES = {
    "run_run_once_strategies",
    "_residual_sleeve_config",
    # the EB live pending-order view, threaded into the run_once call site
    "_eb_live_portfolio_view",
    # the live-only hold on EB's decision outside regular hours
    "_eb_decision_outside_rth",
    # `conditions` UNION `config`, the merge the EB config readers share
    "_merged_strategy_settings",
    # paths these tests never take, but a NameError does not care
    "_llm_resolution_is_fatal",
    "_resolve_nexus_runtime_identity",
    "_run_graph_nexus_with_point_in_time",
}


def _dispatcher_namespace(run_mode="backtest"):
    tree = ast.parse(BROKER_PATH.read_text())
    wanted = _DISPATCHER_NAMES
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert {node.name for node in nodes} == wanted
    captured = {}
    received_modes = []

    class CapturingStrategyX:
        def run_once(self, *args, **kwargs):
            captured.update(args[3])
            received_modes.append(kwargs.get("mode"))
            return {}

    namespace = {
        "MODE_BACKTEST": "backtest",
        "MODE_LIVE": "live",
        "mode": run_mode,
        "os": __import__("os"),
        "_strategy_cache": {},
        "_strategy_class_cache": {"strategy_x": CapturingStrategyX},
        "_log": lambda *args, **kwargs: None,
        "_load_strategy_class": lambda name: CapturingStrategyX,
        "_apply_regime_profile": lambda config, regime: dict(config),
        "_apply_live_overrides": lambda config: dict(config),
        "_instance_kind_and_crypto_config": lambda: ("stock", {}),
        "instance_id": "main",
        "backtest_row_id": "bt-1",
        "telemetry_llm_call_context": lambda **kwargs: nullcontext(),
        "get_conn": lambda: pytest.fail("model resolution should not run"),
        "resolve_model_refs_in_config": lambda conn, config: config,
        "_partial_trim_syms": lambda sizes: set(),
        "_chop_ret20_cfg": lambda config: None,
    }
    for node in nodes:
        exec(compile(ast.Module([node], []), str(BROKER_PATH), "exec"), namespace)
    namespace["captured"] = captured
    namespace["received_modes"] = received_modes
    return namespace


def _dispatch(strategy_name="strategy_x", **config):
    namespace = _dispatcher_namespace()
    namespace["_strategy_class_cache"][strategy_name] = (
        namespace["_strategy_class_cache"]["strategy_x"]
    )
    namespace["run_run_once_strategies"](
        [{"strategy": strategy_name, "weight": 1.0, "config": config}],
        ["QQQ", "TQQQ", "SPY"],
        {"QQQ": 400.0, "TQQQ": 50.0, "SPY": 500.0},
        datetime(2026, 6, 1, 20, tzinfo=timezone.utc),
        data={}, portfolio_emulator=object(), strategy_caches={},
        mode="backtest",
    )
    return namespace["captured"]


def test_same_enabled_residual_bear_symbol_injects_conflict():
    captured = _dispatch(
        strategy_x_enabled=True,
        bear_kicker_symbol=" sqqq ",
        residual_sleeve_enabled=True,
        residual_sleeve_bear_symbol="SQQQ",
    )
    assert captured["_strategy_x_bear_residual_conflict"] is True


def test_pascalcase_strategy_name_cannot_bypass_conflict_injection():
    captured = _dispatch(
        strategy_name="StrategyX",
        strategy_x_enabled=True,
        residual_sleeve_enabled=True,
        residual_sleeve_bear_symbol="SQQQ",
    )
    assert captured["_strategy_x_bear_residual_conflict"] is True


@pytest.mark.parametrize(
    "residual_enabled,residual_symbol",
    [(False, "SQQQ"), (True, "SH")],
)
def test_disabled_or_different_residual_bear_symbol_is_not_a_conflict(
    residual_enabled, residual_symbol,
):
    captured = _dispatch(
        strategy_x_enabled=True,
        bear_kicker_symbol="SQQQ",
        residual_sleeve_enabled=residual_enabled,
        residual_sleeve_bear_symbol=residual_symbol,
    )
    assert captured.get("_strategy_x_bear_residual_conflict") is not True


def test_unverifiable_residual_configuration_fails_closed():
    namespace = _dispatcher_namespace()
    namespace["_residual_sleeve_config"] = lambda specs: (_ for _ in ()).throw(
        ValueError("bad residual config")
    )
    namespace["run_run_once_strategies"](
        [{"strategy": "strategy_x", "weight": 1.0,
          "config": {"strategy_x_enabled": True}}],
        ["QQQ"], {"QQQ": 400.0},
        datetime(2026, 6, 1, 20, tzinfo=timezone.utc),
        data={}, portfolio_emulator=object(), strategy_caches={}, mode="backtest",
    )
    assert namespace["captured"]["_strategy_x_bear_residual_conflict"] is True


def test_strategy_x_state_error_propagates_and_invalidates_broker_run():
    namespace = _dispatcher_namespace()

    class InvalidStrategyX:
        def run_once(self, *args, **kwargs):
            raise BearSystemStateError("unprovenanced SQQQ")

    namespace["_strategy_class_cache"]["strategy_x"] = InvalidStrategyX
    with pytest.raises(BearSystemStateError, match="unprovenanced SQQQ"):
        namespace["run_run_once_strategies"](
            [{"strategy": "strategy_x", "weight": 1.0,
              "config": {"strategy_x_enabled": True}}],
            ["QQQ"], {"QQQ": 400.0},
            datetime(2026, 6, 1, 20, tzinfo=timezone.utc),
            data={}, portfolio_emulator=object(), strategy_caches={},
            mode="backtest",
        )


@pytest.mark.parametrize("strategy_name", ["strategy_x", "StrategyX"])
def test_production_backtest_call_translates_run_mode_for_strategy_x_aliases(
    strategy_name,
):
    namespace = _dispatcher_namespace(run_mode="live")
    namespace["mode"] = namespace["MODE_BACKTEST"]
    namespace["_strategy_class_cache"][strategy_name] = (
        namespace["_strategy_class_cache"]["strategy_x"]
    )
    namespace["run_run_once_strategies"](
        [{"strategy": strategy_name, "weight": 1.0, "config": {}}],
        ["QQQ"], {"QQQ": 400.0},
        datetime(2026, 6, 1, 20, tzinfo=timezone.utc),
        data={}, portfolio_emulator=object(), strategy_caches={},
    )
    assert namespace["received_modes"] == ["backtest"]


def test_production_backtest_call_preserves_legacy_none_for_other_strategies():
    namespace = _dispatcher_namespace(run_mode="live")
    namespace["mode"] = namespace["MODE_BACKTEST"]
    namespace["_strategy_class_cache"]["other_strategy"] = (
        namespace["_strategy_class_cache"]["strategy_x"]
    )
    namespace["run_run_once_strategies"](
        [{"strategy": "other_strategy", "weight": 1.0, "config": {}}],
        ["QQQ"], {"QQQ": 400.0},
        datetime(2026, 6, 1, 20, tzinfo=timezone.utc),
        data={}, portfolio_emulator=object(), strategy_caches={},
    )
    assert namespace["received_modes"] == [None]


@pytest.mark.parametrize("scheduler_mode", [None, "IDLE", "FULL", "MONITOR"])
def test_live_strategy_x_preserves_scheduler_mode(scheduler_mode):
    namespace = _dispatcher_namespace(run_mode="backtest")
    namespace["mode"] = namespace["MODE_LIVE"]
    namespace["run_run_once_strategies"](
        [{"strategy": "strategy_x", "weight": 1.0, "config": {}}],
        ["QQQ"], {"QQQ": 400.0},
        datetime(2026, 6, 1, 20, tzinfo=timezone.utc),
        data={}, portfolio_emulator=object(), strategy_caches={},
        mode=scheduler_mode,
    )
    assert namespace["received_modes"] == [scheduler_mode]
    assert namespace["received_modes"] != ["backtest"]


@pytest.mark.parametrize("scheduler_mode", [None, "IDLE", "FULL", "MONITOR"])
def test_live_sibling_strategy_preserves_scheduler_mode(scheduler_mode):
    namespace = _dispatcher_namespace(run_mode="backtest")
    namespace["mode"] = namespace["MODE_LIVE"]
    namespace["_strategy_class_cache"]["sibling_strategy"] = (
        namespace["_strategy_class_cache"]["strategy_x"]
    )
    namespace["run_run_once_strategies"](
        [{"strategy": "sibling_strategy", "weight": 1.0, "config": {}}],
        ["QQQ"], {"QQQ": 400.0},
        datetime(2026, 6, 1, 20, tzinfo=timezone.utc),
        data={}, portfolio_emulator=object(), strategy_caches={},
        mode=scheduler_mode,
    )
    assert namespace["received_modes"] == [scheduler_mode]


# --- the harness itself must fail loudly, not quietly ----------------------
#
# `run_run_once_strategies` is AST-extracted into a stub namespace, and its
# per-strategy body is wrapped in a blanket `except Exception` that logs RED
# and moves on (broker.py, "Run-once strategy '{name}' error"). So a broker
# helper added to that function and NOT added to `_DISPATCHER_NAMES` below
# raises NameError, the handler eats it, and EVERY test here sees
# `received_modes == []` — 15 failures with one cause and no clue in any of
# them. That is how `_eb_live_portfolio_view` (cb9d2ba) broke this file.

def _free_names(function_name):
    """Module-scope names the extracted function reads but never binds."""
    import builtins

    tree = ast.parse(BROKER_PATH.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == function_name)
    bound = {a.arg for a in list(fn.args.args) + list(fn.args.kwonlyargs)}
    for slot in (fn.args.vararg, fn.args.kwarg):
        if slot is not None:
            bound.add(slot.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    loads = {n.id for n in ast.walk(fn)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return loads - bound - set(dir(builtins))


def test_every_name_the_dispatcher_reads_is_in_the_harness():
    """The sentinel. Fails on the commit that adds an unstubbed helper, naming
    it — instead of 15 assertion failures that all say `[] != [None]`."""
    namespace = _dispatcher_namespace()
    missing = sorted(_free_names("run_run_once_strategies") - set(namespace))
    assert not missing, (
        "run_run_once_strategies reads module-scope names this harness does "
        "not provide: " + ", ".join(missing) + ". Add them to the extracted "
        "set or stub them; a NameError here is swallowed by the dispatcher's "
        "blanket handler and every test in this file fails with an empty "
        "result instead.")


def test_the_swallowing_handler_is_still_the_reason_this_matters():
    """A source assertion: if the blanket handler ever goes away, the sentinel
    above stops being load-bearing and this test should be revisited."""
    source = BROKER_PATH.read_text()
    assert "Run-once strategy '{name}' error" in source.replace('f"', '"')
