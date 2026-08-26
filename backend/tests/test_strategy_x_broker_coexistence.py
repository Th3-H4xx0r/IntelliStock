"""Broker ownership guard between Strategy X and the residual bear sleeve."""
from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest

from strategy_x_bear import BearSystemStateError


BROKER_PATH = Path(__file__).resolve().parents[1] / "broker.py"


def _dispatcher_namespace():
    tree = ast.parse(BROKER_PATH.read_text())
    wanted = {"run_run_once_strategies", "_residual_sleeve_config"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert {node.name for node in nodes} == wanted
    captured = {}

    class CapturingStrategyX:
        def run_once(self, *args, **kwargs):
            captured.update(args[3])
            return {}

    namespace = {
        "MODE_BACKTEST": "backtest",
        "MODE_LIVE": "live",
        "mode": "backtest",
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
