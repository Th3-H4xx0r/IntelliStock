"""Per-strategy-document live risk limits.

A strategy designed to ride a -30% drawdown cannot live under a 5% soft
buy-freeze, and a 65%-of-NAV core cannot be built under a 10% per-order cap. The
gate keeps BLOCKING rather than clipping; the cap is simply set to what the
strategy asks for. Every other document must keep the module defaults.
"""
import ast
import datetime
import os
import sys
from decimal import Decimal

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_risk_state import (  # noqa: E402
    DEFAULT_LEVERAGED_SYMBOLS,
    DEFAULT_RISK_LIMITS,
    RiskLimits,
    evaluate_drawdown,
    initialize_risk_state,
)

T0 = datetime.datetime(2026, 6, 3, 14, 0, tzinfo=datetime.timezone.utc)
T1 = datetime.datetime(2026, 6, 3, 15, 0, tzinfo=datetime.timezone.utc)
EB = RiskLimits(max_order_fraction="0.70", max_symbol_fraction="0.70",
                max_leveraged_fraction="0.70", soft="0.25", hard="0.35",
                kill="0.45")


def test_the_module_defaults_are_exactly_todays_numbers():
    assert DEFAULT_RISK_LIMITS.max_order_fraction == Decimal("0.10")
    assert DEFAULT_RISK_LIMITS.max_symbol_fraction == Decimal("0.20")
    assert DEFAULT_RISK_LIMITS.max_leveraged_fraction == Decimal("0.10")
    assert DEFAULT_RISK_LIMITS.soft == Decimal("0.05")
    assert DEFAULT_RISK_LIMITS.hard == Decimal("0.09")
    assert DEFAULT_RISK_LIMITS.kill == Decimal("0.12")


def test_bootstrap_without_limits_is_unchanged():
    state = initialize_risk_state("i", "a", Decimal("6000"), T0)
    assert state.max_order_notional == Decimal("600.00")
    assert state.max_symbol_notional == Decimal("1200.00")
    assert state.max_leveraged_notional == Decimal("600.00")


def test_bootstrap_with_eb_limits_uses_them():
    state = initialize_risk_state("i", "a", Decimal("6000"), T0, limits=EB)
    assert state.max_order_notional == Decimal("4200.00")
    assert state.max_symbol_notional == Decimal("4200.00")
    assert state.max_leveraged_notional == Decimal("4200.00")


def test_the_override_survives_an_evaluate_drawdown_refresh():
    """The refresh re-derives caps from fractions every tick; an override in
    only one place is overwritten on the next observation."""
    state = initialize_risk_state("i", "a", Decimal("6000"), T0, limits=EB)
    refreshed = evaluate_drawdown(state, Decimal("6000"), T1, limits=EB)
    assert refreshed.max_order_notional == Decimal("4200.00")
    assert refreshed.max_leveraged_notional == Decimal("4200.00")


def test_a_document_with_no_limits_still_refreshes_to_module_defaults():
    state = initialize_risk_state("i", "a", Decimal("6000"), T0)
    refreshed = evaluate_drawdown(state, Decimal("5000"), T1)
    assert refreshed.max_symbol_notional == Decimal("1000.00")


def test_the_eb_drawdown_ladder_replaces_the_module_one():
    """-26% is 'kill' under the module defaults and merely 'soft' under EB's."""
    state = initialize_risk_state("i", "a", Decimal("10000"), T0, limits=EB)
    refreshed = evaluate_drawdown(state, Decimal("7400"), T1, limits=EB)
    assert refreshed.level == "soft"
    default_state = initialize_risk_state("i", "a", Decimal("10000"), T0)
    assert evaluate_drawdown(default_state, Decimal("7400"), T1).level == "kill"


def test_explicit_thresholds_still_win_over_limits():
    """Every existing caller passes soft/hard/kill positionally-by-keyword."""
    state = initialize_risk_state("i", "a", Decimal("10000"), T0)
    got = evaluate_drawdown(state, Decimal("9000"), T1,
                            soft_threshold=Decimal("0.30"),
                            hard_threshold=Decimal("0.40"),
                            kill_threshold=Decimal("0.50"))
    assert got.level == "normal"


@pytest.mark.parametrize("kwargs", [
    {"soft": "0.35", "hard": "0.25"},          # not increasing
    {"kill": "1.5"},                            # outside (0, 1)
    {"max_order_fraction": "0"},                # a zero cap is not a cap
    {"max_symbol_fraction": "1.5"},
    {"soft": "nope"},
])
def test_a_malformed_limit_set_is_refused_at_construction(kwargs):
    with pytest.raises(ValueError):
        RiskLimits(**kwargs)


def test_qld_is_a_leveraged_symbol():
    assert "QLD" in DEFAULT_LEVERAGED_SYMBOLS
    assert {"TQQQ", "SQQQ", "SPXU", "UPRO", "SOXL", "SOXS"} <= DEFAULT_LEVERAGED_SYMBOLS


def test_the_broker_no_longer_inlines_its_own_leveraged_set():
    """The inline literal at broker.py:9236-9241 could not gain QLD without
    someone remembering two places."""
    source = open(os.path.join(_backend, "broker.py")).read()
    assert '"SQQQ", "TQQQ", "SPXU", "UPRO", "SOXL", "SOXS"' not in source
    assert "DEFAULT_LEVERAGED_SYMBOLS" in source


def _extract(*names):
    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in names]
    assert wanted, f"none of {names} found in broker.py"
    ns = {"_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), broker, "exec"), ns)
    return ns


def test_the_broker_reads_the_eb_lanes_limits():
    ns = _extract("_strategy_eb_risk_limits")
    limits = ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}])
    assert limits == EB


def test_no_eb_lane_means_no_override():
    ns = _extract("_strategy_eb_risk_limits")
    for specs in (None, [], [{"strategy": "graph_nexus_analysis", "config": {}}],
                  [{"strategy": "strategy_eb",
                    "config": {"strategy_eb_enabled": False}}]):
        assert ns["_strategy_eb_risk_limits"](specs) is None, specs


def test_a_malformed_eb_limit_set_degrades_to_the_module_defaults():
    """A typo in a config value must not take the live loop down; it must fall
    back to the TIGHTER module defaults, never to no limit."""
    ns = _extract("_strategy_eb_risk_limits")
    assert ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb",
          "config": {"strategy_eb_enabled": True,
                     "live_soft_drawdown": 0.9}}]) is None
