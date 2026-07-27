"""A close-derived decision cannot execute on the event that produced it."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path


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


def test_close_derived_order_is_scheduled_after_decision_event():
    from event_time import SimulationClock, bar_available_at

    bar = {"t": "2026-03-02T14:00:00Z", "c": 123.0}
    decision_at = bar_available_at(
        bar,
        interval=timedelta(hours=1),
        session_close_resolver=None,
    )
    clock = SimulationClock(
        decision_at=decision_at,
        available_through=decision_at,
        execute_not_before=decision_at + timedelta(hours=1),
    )

    assert decision_at == datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
    assert clock.execute_not_before > clock.decision_at


def test_broker_price_resolution_waits_for_hour_bar_close():
    import datetime as datetime_module

    from bar_time import bar_time_to_datetime

    namespace = {
        "datetime": datetime_module,
        "MODE_BACKTEST": "backtest",
        "mode": "backtest",
        "_backtest_alpaca_timeframe": "1Hour",
        "_is_crypto_instance_runtime": lambda: False,
        "_current_time_to_utc": lambda value: value,
        "_bar_time_to_datetime": bar_time_to_datetime,
    }
    _load_broker_functions(
        "_aware_backtest_clock",
        "_backtest_bar_interval",
        "_backtest_bar_availability_resolver",
        "_get_prices_at_time",
        namespace=namespace,
    )
    data = {"SPY": [{"t": "2026-03-02T14:00:00Z", "c": 123.0}]}

    assert namespace["_get_prices_at_time"](
        data,
        ["SPY"],
        datetime(2026, 3, 2, 14, 59, 59),
    ) == {}
    assert namespace["_get_prices_at_time"](
        data,
        ["SPY"],
        datetime(2026, 3, 2, 15, 0),
    ) == {"SPY": 123.0}
