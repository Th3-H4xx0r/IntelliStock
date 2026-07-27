from __future__ import annotations

import ast
import datetime as datetime_module
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]


def _load_broker_functions(*names, namespace):
    tree = ast.parse((BACKEND / "broker.py").read_text(encoding="utf-8"))
    wanted = set(names)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    assert {node.name for node in nodes} == wanted
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), "broker.py", "exec"),
        namespace,
    )
    return namespace


def _event_namespace():
    from bar_time import bar_time_to_datetime

    return {
        "datetime": datetime_module,
        "MODE_BACKTEST": "backtest",
        "mode": "backtest",
        "_backtest_alpaca_timeframe": "1Hour",
        "_is_non_equity_instance_runtime": lambda: False,
        "_current_time_to_utc": lambda value: value,
        "_bar_time_to_datetime": bar_time_to_datetime,
    }


def test_broker_price_events_keep_the_real_bar_availability_timestamp():
    namespace = _event_namespace()
    _load_broker_functions(
        "_aware_backtest_clock",
        "_backtest_bar_interval",
        "_equity_daily_bar_session_close",
        "_backtest_bar_availability_resolver",
        "_get_price_events_at_time",
        namespace=namespace,
    )
    data = {"SPY": [{"t": "2026-03-02T14:00:00Z", "c": 100.0}]}

    at_close = namespace["_get_price_events_at_time"](
        data,
        ["SPY"],
        datetime(2026, 3, 2, 15, 0),
    )
    synthetic_later_tick = namespace["_get_price_events_at_time"](
        data,
        ["SPY"],
        datetime(2026, 3, 2, 15, 30),
    )

    assert at_close["SPY"].price == 100.0
    assert at_close["SPY"].available_at == datetime(
        2026, 3, 2, 15, 0, tzinfo=timezone.utc
    )
    assert synthetic_later_tick["SPY"] == at_close["SPY"]


def test_missing_next_bar_cannot_fill_on_a_synthetic_tick():
    from portfolio_emulator import create_backtest_emulator

    namespace = _event_namespace()
    _load_broker_functions(
        "_aware_backtest_clock",
        "_backtest_bar_interval",
        "_equity_daily_bar_session_close",
        "_backtest_bar_availability_resolver",
        "_get_price_events_at_time",
        namespace=namespace,
    )
    data = {"SPY": [{"t": "2026-03-02T14:00:00Z", "c": 100.0}]}
    decision_at = datetime(2026, 3, 2, 15, 0)
    emulator = create_backtest_emulator(
        initial_cash=1_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(0),
    )
    events = namespace["_get_price_events_at_time"](
        data,
        ["SPY"],
        decision_at,
    )
    receipt = emulator.execute_signal(
        "SPY",
        1,
        events["SPY"].price,
        timestamp=decision_at,
        cash_per_trade=100.0,
        order_source="main_signal",
    )

    assert receipt.accepted and not receipt.filled
    assert emulator.process_price_events(events) == ()
    assert emulator.process_price_events(
        namespace["_get_price_events_at_time"](
            data,
            ["SPY"],
            datetime(2026, 3, 2, 16, 0),
        )
    ) == ()
    assert emulator.get_cash() == 1_000.0
    assert emulator.get_positions() == {}


def test_new_bar_event_after_decision_is_the_only_fill_trigger():
    from portfolio_emulator import create_backtest_emulator

    namespace = _event_namespace()
    _load_broker_functions(
        "_aware_backtest_clock",
        "_backtest_bar_interval",
        "_equity_daily_bar_session_close",
        "_backtest_bar_availability_resolver",
        "_get_price_events_at_time",
        namespace=namespace,
    )
    data = {
        "SPY": [
            {"t": "2026-03-02T14:00:00Z", "c": 100.0},
            {"t": "2026-03-02T15:00:00Z", "c": 110.0},
        ]
    }
    emulator = create_backtest_emulator(
        initial_cash=1_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(0),
    )
    emulator.execute_signal(
        "SPY",
        1,
        100.0,
        timestamp=datetime(2026, 3, 2, 15, 0),
        cash_per_trade=100.0,
        order_source="main_signal",
    )

    fills = emulator.process_price_events(
        namespace["_get_price_events_at_time"](
            data,
            ["SPY"],
            datetime(2026, 3, 2, 16, 0),
        )
    )

    assert len(fills) == 1
    assert fills[0].quote_timestamp == datetime(
        2026, 3, 2, 16, 0, tzinfo=timezone.utc
    )
    assert fills[0].price > 110.0


def test_all_broker_emissions_route_through_the_source_aware_helper():
    tree = ast.parse((BACKEND / "broker.py").read_text(encoding="utf-8"))
    direct_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "execute_signal"
            and isinstance(func.value, ast.Name)
            and func.value.id == "portfolio_emulator"
        ):
            direct_calls.append(node.lineno)

    assert direct_calls == []
    source = (BACKEND / "broker.py").read_text(encoding="utf-8")
    for expected in (
        "scheduled_start",
        "scheduled_same_bar",
        "residual_bear",
        "residual_bull",
        "main_signal",
    ):
        assert expected in source


def test_confirmed_scheduled_fill_updates_strategy_ownership_only_at_fill():
    from simulated_execution import SimulationFill

    strategy_cache = {"earnings": {"_earnings_positions": {}}}
    namespace = {
        "_strategy_cache": strategy_cache,
        "_RESIDUAL_SLEEVE_STATE": {},
    }
    _load_broker_functions(
        "_apply_backtest_confirmed_fill_state",
        namespace=namespace,
    )
    fill = SimulationFill(
        order_id="scheduled",
        symbol="SPY",
        side="buy",
        incremental_quantity=2.0,
        cumulative_quantity=2.0,
        price=101.0,
        fees=0.1,
        spread_cost=0.1,
        slippage_cost=0.1,
        quote_timestamp=datetime(2026, 3, 2, 16, 0, tzinfo=timezone.utc),
        executed_at=datetime(2026, 3, 2, 16, 0, tzinfo=timezone.utc),
        cost_model_version="test-v1",
        source="scheduled_start:earnings",
    )

    namespace["_apply_backtest_confirmed_fill_state"](fill)

    assert strategy_cache["earnings"]["_earnings_positions"]["SPY"] == 2.0
