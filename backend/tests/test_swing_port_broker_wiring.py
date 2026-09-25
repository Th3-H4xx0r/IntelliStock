"""broker.py wiring for next-open fills and bracket legs (spec 6.2).

broker.py is not import-safe (argparse and the main loop run at import), so
the functions under test are AST-extracted into a namespace that stubs what
they read, as test_strategy_x_broker_coexistence does. `_EXTRACTED` lists
them; `test_every_name_the_extracted_helpers_read_is_provided` fails, naming
the name, the day one of them starts reading an unstubbed global.
"""
from __future__ import annotations

import ast
import builtins
import datetime as datetime_module
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
_tests = os.path.dirname(os.path.abspath(__file__))
if _tests not in sys.path:
    sys.path.insert(0, _tests)

import backtest_bar_events as bbe  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
)
from swing_port_calendar_fixtures import AVAILABLE, daily_bar  # noqa: E402

BROKER_PATH = Path(__file__).resolve().parents[1] / "broker.py"
UTC = timezone.utc

_EXTRACTED = {
    "_submit_portfolio_signal",
    "_backtest_bar_open_resolver",
    "_process_backtest_bar_events",
    "_core_sleeve_cfg_raw",
    "_backtest_credit_pending_sell_proceeds",
}


def _namespace():
    tree = ast.parse(BROKER_PATH.read_text())
    nodes = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in _EXTRACTED]
    assert {n.name for n in nodes} == _EXTRACTED
    applied, logged = [], []
    namespace = {
        "datetime": datetime_module,
        "_bbe": bbe,
        "_bar_time_to_datetime": bar_time_to_datetime,
        "_backtest_bar_interval": lambda: timedelta(days=1),
        "_aware_backtest_clock": (
            lambda t: t.replace(tzinfo=UTC) if t.tzinfo is None
            else t.astimezone(UTC)),
        "_backtest_bar_availability_resolver": lambda: AVAILABLE,
        "_backtest_fill_snapshot_marks": (
            lambda portfolio, prices, data, now: dict(prices or {})),
        "_apply_backtest_confirmed_fill_state": (
            lambda fill, marks: applied.append((fill, marks))),
        "_log": lambda message, color=None: logged.append(message),
        "applied": applied,
        "logged": logged,
    }
    for node in nodes:
        exec(compile(ast.Module([node], []), str(BROKER_PATH), "exec"),
             namespace)
    return namespace


def _free_names(function_name):
    tree = ast.parse(BROKER_PATH.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == function_name)
    bound = {a.arg for a in list(fn.args.args) + list(fn.args.kwonlyargs)}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    loads = {n.id for n in ast.walk(fn)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return loads - bound - set(dir(builtins))


def test_every_name_the_extracted_helpers_read_is_provided():
    namespace = _namespace()
    for name in sorted(_EXTRACTED):
        missing = sorted(_free_names(name) - set(namespace))
        assert not missing, f"{name} reads unstubbed names: {missing}"


class _Recorder:
    has_next_event_execution = True

    def __init__(self):
        self.calls = []

    def execute_signal(self, ticker, signal, price, **kwargs):
        self.calls.append(kwargs)
        return True


@pytest.mark.parametrize("hints", [None, {}])
def test_an_eb_style_submission_passes_exactly_what_it_did(hints):
    ns = _namespace()
    portfolio = _Recorder()
    ns["_submit_portfolio_signal"](
        portfolio, "TQQQ", 1, 50.0, timestamp="t", cash_per_trade=1234.5,
        sell_fraction=1.0, order_source="main_signal",
        execution_hints=bbe.execution_hint_kwargs({"buy_cash": 1234.5}, 1)
        if hints is None else hints)
    assert portfolio.calls == [{"timestamp": "t", "cash_per_trade": 1234.5,
                                "sell_fraction": 1.0,
                                "order_source": "main_signal"}]


def test_a_swing_submission_forwards_the_hints():
    ns = _namespace()
    portfolio = _Recorder()
    hints = bbe.execution_hint_kwargs(
        {"buy_cash": 1250.0, "whole_shares": True, "fill_at_next_open": True,
         "bracket": {"take_profit_price": 109.0, "stop_loss_price": 94.0}}, 1)
    ns["_submit_portfolio_signal"](
        portfolio, "AAPL", 1, 100.0, timestamp="t", cash_per_trade=1250.0,
        order_source="main_signal", execution_hints=hints)
    [kwargs] = portfolio.calls
    assert kwargs["whole_shares"] is True
    assert kwargs["fill_at_next_open"] is True
    assert kwargs["bracket"] == {"take_profit_price": 109.0,
                                 "stop_loss_price": 94.0}


def test_a_legacy_emulator_never_sees_the_hints():
    ns = _namespace()
    portfolio = _Recorder()
    portfolio.has_next_event_execution = False
    ns["_submit_portfolio_signal"](
        portfolio, "BTC/USD", 1, 100.0, timestamp="t",
        order_source="main_signal", execution_hints={"whole_shares": True})
    assert portfolio.calls == [{"timestamp": "t", "cash_per_trade": 1000.0,
                                "sell_fraction": 1.0}]


def test_the_bar_hook_fills_at_the_session_open_and_reports_each_fill():
    bbe.reset_label_cache()
    ns = _namespace()
    emu = PortfolioEmulator(
        10_000.0,
        execution_simulator=NextEventExecutionSimulator(ExecutionCostModel(
            version="test-v1", spread_bps=20.0, slippage_bps=10.0,
            fee_bps=5.0, latency=timedelta(0))),
        execution_delay=timedelta(days=1))
    monday_tick = datetime(2026, 3, 2, 13, 0)             # naive UTC clock
    emu.execute_signal("AAPL", 1, 100.0, timestamp=monday_tick,
                       cash_per_trade=1_250.0, order_source="main_signal",
                       whole_shares=True, fill_at_next_open=True,
                       bracket={"take_profit_price": 109.0,
                                "stop_loss_price": 94.0})
    data = {"AAPL": [daily_bar("2026-02-27", 99, 100, 98, 100),
                     daily_bar("2026-03-02", 100, 101, 93, 95),
                     daily_bar("2026-03-03", 95, 96, 94.5, 95)]}
    fills = ns["_process_backtest_bar_events"](
        emu, data, {"AAPL": 95.0}, datetime(2026, 3, 3, 13, 0))
    assert [(f.side, f.quote_timestamp) for f in fills] == [
        ("buy", datetime(2026, 3, 2, 14, 30, tzinfo=UTC)),
        ("sell", datetime(2026, 3, 2, 21, 0, tzinfo=UTC)),
    ]
    assert fills[1].source.startswith("bracket_sl:")
    assert [fill for fill, _marks in ns["applied"]] == list(fills)
    assert ns["applied"][0][1] == {"AAPL": 95.0}
    assert "exit_reason=stop_loss" in ns["logged"][1]
    # Tuesday is not complete at Tuesday 05:00 PT: nothing more, no error.
    assert ns["_process_backtest_bar_events"](
        emu, data, {}, datetime(2026, 3, 3, 13, 0)) == ()


def test_the_bar_hook_is_a_no_op_while_a_symbol_has_no_new_bar():
    bbe.reset_label_cache()
    ns = _namespace()
    emu = PortfolioEmulator(
        1_000.0,
        execution_simulator=NextEventExecutionSimulator(ExecutionCostModel(
            version="test-v1", spread_bps=0.0, slippage_bps=0.0,
            fee_bps=0.0, latency=timedelta(0))),
        execution_delay=timedelta(days=1))
    emu.execute_signal("HALT", 1, 10.0, timestamp=datetime(2026, 3, 2, 13, 0),
                       cash_per_trade=100.0, order_source="main_signal",
                       fill_at_next_open=True)
    assert ns["_process_backtest_bar_events"](
        emu, {"HALT": [daily_bar("2026-02-27", 10, 10, 10, 10)]}, {},
        datetime(2026, 3, 9, 13, 0)) == ()
    assert emu.pending_execution_symbols() == ("HALT",)


# -- source-level placement: the main loop cannot be executed in a test ------

_SOURCE = BROKER_PATH.read_text()


def test_the_bar_hook_runs_after_pending_fills_and_before_the_strategies():
    call = _SOURCE.index("_process_backtest_bar_events(\n"
                         "                    portfolio_emulator, data, prices, current_time)")
    assert _SOURCE.index("_reconcile_anchor_pending_orders(portfolio_emulator)") < call
    assert call < _SOURCE.index("run_once_results = run_run_once_strategies(")
    guard = _SOURCE.rindex("if (portfolio_emulator.has_bracket_legs()", 0, call)
    assert call - guard < 200
    # Defined before the module-level loop reaches it.
    assert _SOURCE.index("def _process_backtest_bar_events(") < call
    assert _SOURCE.index("import backtest_bar_events as _bbe") < call


def test_only_the_main_backtest_submission_forwards_hints():
    assert _SOURCE.count("execution_hints=_bbe.execution_hint_kwargs(") == 1
    site = _SOURCE.index("execution_hints=_bbe.execution_hint_kwargs(")
    assert _SOURCE.rindex("_mpg_result = _submit_portfolio_signal(", 0, site) > \
        _SOURCE.rindex("_anchor_order_source = (", 0, site)


def test_the_deploy_check_hashes_the_backtest_engine_files():
    """Spec 12: a push that changes only the simulator must not read as
    deployed before the image carrying it exists."""
    checked = (BROKER_PATH.parents[1] / "scripts"
               / "check_deployed_code.py").read_text()
    served = (BROKER_PATH.parent / "api" / "main.py").read_text()
    for rel in ("simulated_execution.py", "portfolio_emulator.py",
                "backtest_bar_events.py"):
        assert f'"backend/{rel}"' in checked
        assert f'    "{rel}",' in served


# -- the same-tick funding credit (plan B pre-flight R1/F10) -----------------

_CREDIT = "backtest_credit_pending_sell_proceeds"


def _lane(strategy, config, *, weight=1.0, scope="run_once"):
    return {"strategy": strategy, "weight": weight, "execution_position": 10,
            "decision_phase": "pre", "execution_scope": scope,
            "conditions": {}, "config": config}


@pytest.mark.parametrize("specs, expected", [
    # The swing lab: one strategy_swing lane carrying the flag.
    ([_lane("strategy_swing", {"strategy_swing_enabled": True, _CREDIT: True})],
     True),
    # No lane sets it: off, as every document without the key always was.
    ([_lane("strategy_swing", {"strategy_swing_enabled": True})], False),
    ([_lane("strategy_eb", {"strategy_eb_enabled": True})], False),
    ([], False),
    (None, False),
    # Doc 200's shape: EB plus a weight-0 graph_nexus lane that never runs and
    # only carries the flag. Read exactly as before, so EB keeps its credit.
    ([_lane("strategy_eb", {"strategy_eb_enabled": True}),
      _lane("graph_nexus_analysis", {_CREDIT: True}, weight=0.0)], True),
    # A graph_nexus lane that says False is not overridden by the absence of
    # the key elsewhere.
    ([_lane("graph_nexus_analysis", {_CREDIT: False}),
      _lane("strategy_eb", {"strategy_eb_enabled": True})], False),
    # A lane the dispatcher would skip is not an enabled lane.
    ([_lane("strategy_swing", {_CREDIT: True}, weight=0.0)], False),
    ([_lane("strategy_swing", {_CREDIT: True}, scope="per_symbol")], False),
    ([_lane("strategy_swing", {_CREDIT: True}, weight="bad")], False),
])
def test_any_enabled_run_once_lane_may_turn_the_funding_credit_on(
        specs, expected):
    ns = _namespace()
    assert ns["_backtest_credit_pending_sell_proceeds"](specs) is expected


def test_the_emulator_credit_is_read_through_the_any_lane_helper():
    assert _SOURCE.count(
        "portfolio_emulator.credit_pending_sell_proceeds = (\n"
        "                        _backtest_credit_pending_sell_proceeds(\n"
        "                            _cached_strategies))") == 1
    # The anchor policy's own read stays graph_nexus-only.
    policy = _SOURCE[_SOURCE.index("def _anchor_reinforcement_execution_policy("):
                     _SOURCE.index("def _anchor_reinforcement_position_headroom(")]
    assert 'cfg = _core_sleeve_cfg_raw(cached_strategies) or {}' in policy
    assert "_backtest_credit_pending_sell_proceeds" not in policy
