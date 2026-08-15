import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.levers import lever_surface, levers_from_schema

# Real header shape, copied from backend/strategies/rsi.py line 1.
_RSI = {"strategy": "Rsi", "weight": 0.5, "execution_position": 0,
        "conditions": {}, "config": {"period": 14, "oversold": 30,
                                     "overbought": 70, "use_midline": False}}


def test_each_declared_config_key_becomes_a_config_lever():
    keys = {l.key for l in levers_from_schema("rsi", _RSI) if l.kind == "config"}
    assert keys == {"period", "oversold", "overbought", "use_midline"}


def test_weight_and_execution_position_and_membership_are_levers_too():
    kinds = {l.kind for l in levers_from_schema("rsi", _RSI)}
    assert {"config", "weight", "execution_position", "membership"} <= kinds


def test_value_types_come_from_the_declared_defaults():
    by_key = {l.key: l for l in levers_from_schema("rsi", _RSI)}
    assert by_key["period"].value_type == "number"
    assert by_key["use_midline"].value_type == "bool"


def test_a_credential_placeholder_is_not_a_tunable_lever():
    schema = {"config": {"llm_api_key": "<optional>", "buy_threshold": 0.15}}
    keys = {l.key for l in levers_from_schema("x", schema) if l.kind == "config"}
    assert keys == {"buy_threshold"}


def test_a_strategy_with_no_schema_yields_no_levers_and_does_not_raise():
    assert levers_from_schema("helper", None) == []
    assert levers_from_schema("helper", {}) == []


def test_lever_surface_spans_every_strategy_it_is_given():
    surface = lever_surface([
        {"id": "rsi", "schema": _RSI},
        {"id": "macd", "schema": {"config": {"fast": 12, "slow": 26}}},
    ])
    assert {l.strategy_id for l in surface} == {"rsi", "macd"}


def test_the_real_registry_produces_levers_for_more_than_one_strategy():
    """Calls production discovery — the point of the design is that it works
    for any strategy, so this must not be mocked."""
    from strategies_meta import get_available_strategies
    surface = lever_surface(get_available_strategies())
    strategies = {l.strategy_id for l in surface}
    assert "rsi" in strategies
    assert len(strategies) > 5
    # Nexus declares far more tunables than RSI; same code path.
    nexus = [l for l in surface if l.strategy_id == "graph_nexus_analysis"]
    rsi = [l for l in surface if l.strategy_id == "rsi"]
    assert len(nexus) > len(rsi) > 0
