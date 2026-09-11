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


#: broker.py is unimportable under pytest, so its top-level helpers are lifted
#: out of the AST and executed in a bare namespace. These are the names those
#: helpers close over.
_BROKER_HELPERS = ("_truthy", "_merged_strategy_settings",
                   "_strategy_eb_risk_limits",
                   "_strategy_eb_universe_symbols",
                   "_strategy_eb_single_position_pct",
                   "_live_risk_limits_for_this_document")
#: Module-level state the helpers read or set. Extracted rather than seeded so
#: each `_extract()` starts from broker.py's own initial values — the
#: once-a-process log flags among them.
_BROKER_TABLES = ("_LANE_ENABLE_FLAGS", "_live_risk_envelope_logged",
                  "_live_risk_defaults_logged", "_live_risk_limits_last_reason")


def _extract(*names):
    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    keep = set(names) | set(_BROKER_HELPERS)
    # Module-level tables the helpers close over (2026-09-02: the lane
    # registry that lets the outlier sleeve widen the envelope beside EB).
    tables = [n for n in tree.body
              if isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id in _BROKER_TABLES]
    wanted = tables + [n for n in tree.body
                       if isinstance(n, ast.FunctionDef) and n.name in keep]
    found = {n.name for n in wanted if isinstance(n, ast.FunctionDef)}
    assert set(names) <= found, f"missing from broker.py: {set(names) - found}"
    ns = {
        "_log": lambda *a, **k: None,
        "_cached_strategies": None,
        "_live_risk_envelope_logged": False,
        "load_strategies_from_db": lambda: ([], None, None),
    }
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


# --- review round 1 -----------------------------------------------------------

@pytest.mark.parametrize("flag", [True, "true", "True", " on ", "yes", "1"])
def test_a_truthy_enabled_flag_widens_the_envelope(flag):
    ns = _extract("_strategy_eb_risk_limits")
    assert ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb",
          "config": {"strategy_eb_enabled": flag}}]) == EB


@pytest.mark.parametrize("flag", [False, "false", "False", "no", "off", "0", ""])
def test_a_string_false_does_not_widen_the_envelope(flag):
    """`bool("false")` is True. The strategy's own `_truthy` calls that string
    DISABLED, so a document storing the flag as a string would sit inert while
    the broker widened its real-money envelope to 70/70/70 on its behalf."""
    ns = _extract("_strategy_eb_risk_limits")
    assert ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb",
          "config": {"strategy_eb_enabled": flag}}]) is None


# --- 2026-09-11 silent-failure audit: D2, D3, D5 ----------------------------

def test_a_lane_missing_from_the_defaults_table_does_not_void_the_document():
    """D5. `defaults_by_lane[name]` was a bare subscript OUTSIDE the per-lane
    `except`, so registering a lane in `_LANE_ENABLE_FLAGS` without a defaults
    row KeyErrored into the outer handler and returned None for the WHOLE
    document — strategy_eb's already-computed envelope thrown away and every
    65%-of-NAV buy blocked on max_order_notional, on real money, silently."""
    ns = _extract("_strategy_eb_risk_limits")
    ns["_LANE_ENABLE_FLAGS"]["ghost_lane"] = "ghost_lane_enabled"
    limits = ns["_strategy_eb_risk_limits"]([
        {"strategy": "ghost_lane", "config": {"ghost_lane_enabled": True}},
        {"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}},
    ])
    assert limits == EB


def test_limits_set_in_conditions_are_seen():
    """D3. The dispatcher merges `conditions` UNDER `config` before handing
    settings to the strategy, so a document that enables EB in `conditions`
    RUNS the strategy — while the envelope reader saw only `config` and left
    the real-money account on the 10/20/10% module defaults."""
    ns = _extract("_strategy_eb_risk_limits")
    assert ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb",
          "conditions": {"strategy_eb_enabled": True},
          "config": {}}]) == EB


def test_config_still_wins_over_conditions():
    ns = _extract("_strategy_eb_risk_limits")
    assert ns["_strategy_eb_risk_limits"](
        [{"strategy": "strategy_eb",
          "conditions": {"strategy_eb_enabled": True},
          "config": {"strategy_eb_enabled": False}}]) is None


def test_the_universe_reader_sees_conditions_too():
    """Same merge, same reason: a lane enabled in `conditions` would trade
    symbols the broker never fetched bars for."""
    ns = _extract("_strategy_eb_universe_symbols")
    got = ns["_strategy_eb_universe_symbols"](
        [{"strategy": "strategy_eb",
          "conditions": {"strategy_eb_enabled": True, "core_symbol": "QLD"},
          "config": {}}])
    assert got[:4] == ["QQQ", "QLD", "SPY", "BIL"]


def test_the_fallback_to_the_module_defaults_names_its_reason():
    """D2. The resolver returned DEFAULT_RISK_LIMITS in silence. That envelope
    blocks every strategy_eb buy on max_order_notional, so the difference
    between "this document declares nothing" and "the lane was skipped by a
    config typo" is the difference between a working account and an inert one."""
    ns = _extract("_live_risk_limits_for_this_document")
    lines = []
    ns["_log"] = lambda msg, color="white": lines.append((str(msg), color))
    ns["_cached_strategies"] = [{"strategy": "strategy_eb",
                                 "config": {"strategy_eb_enabled": True,
                                            "live_soft_drawdown": 0.9}}]
    assert ns["_live_risk_limits_for_this_document"]() is DEFAULT_RISK_LIMITS
    red = [m for m, c in lines if c == "red"]
    assert red, f"the silent fallback: {lines}"
    assert "strategy_eb" in red[0]


def test_the_fallback_line_is_emitted_once_a_process():
    ns = _extract("_live_risk_limits_for_this_document")
    lines = []
    ns["_log"] = lambda msg, color="white": lines.append((str(msg), color))
    ns["_cached_strategies"] = [{"strategy": "graph_nexus_analysis",
                                 "config": {}}]
    for _ in range(5):
        assert ns["_live_risk_limits_for_this_document"]() is DEFAULT_RISK_LIMITS
    assert len([m for m, c in lines if c == "red"]) == 1, lines


def test_one_malformed_sibling_lane_does_not_revert_the_position_cap():
    """D4. The whole loop sat inside one `except Exception: return None`, so a
    single unparseable sibling value reverted the DOCUMENT to the broker's 15%
    failsafe — under which a 65%-of-NAV core buy is trimmed to $0.00 (BT102936),
    which is how Strategy XS shipped inert."""
    ns = _extract("_strategy_eb_single_position_pct")
    cap = ns["_strategy_eb_single_position_pct"]([
        {"strategy": "outlier_sleeve",
         "config": {"outlier_sleeve_enabled": True,
                    "honour_single_position_cap": True,
                    "broker_max_single_position_pct": "not-a-number"}},
        {"strategy": "strategy_eb",
         "config": {"strategy_eb_enabled": True,
                    "honour_single_position_cap": True,
                    "broker_max_single_position_pct": 0.95}},
    ])
    assert cap == 0.95


def test_a_malformed_spec_entry_does_not_revert_the_position_cap():
    ns = _extract("_strategy_eb_single_position_pct")
    cap = ns["_strategy_eb_single_position_pct"]([
        "strategy_eb", None, 17,
        {"strategy": "strategy_eb",
         "config": {"strategy_eb_enabled": True,
                    "honour_single_position_cap": True,
                    "broker_max_single_position_pct": 0.95}},
    ])
    assert cap == 0.95


def test_a_position_cap_set_in_conditions_is_seen():
    ns = _extract("_strategy_eb_single_position_pct")
    assert ns["_strategy_eb_single_position_pct"]([
        {"strategy": "strategy_eb",
         "conditions": {"strategy_eb_enabled": True,
                        "honour_single_position_cap": True,
                        "broker_max_single_position_pct": 0.8},
         "config": {}}]) == 0.8


def test_every_lane_malformed_still_means_no_document_cap():
    ns = _extract("_strategy_eb_single_position_pct")
    assert ns["_strategy_eb_single_position_pct"]([
        {"strategy": "strategy_eb",
         "config": {"strategy_eb_enabled": True,
                    "honour_single_position_cap": True,
                    "broker_max_single_position_pct": "nope"}}]) is None


def test_the_universe_reader_agrees_with_the_limits_reader():
    ns = _extract("_strategy_eb_universe_symbols")
    disabled = [{"strategy": "strategy_eb",
                 "config": {"strategy_eb_enabled": "false"}}]
    enabled = [{"strategy": "strategy_eb",
                "config": {"strategy_eb_enabled": "true"}}]
    assert ns["_strategy_eb_universe_symbols"](disabled) == []
    assert ns["_strategy_eb_universe_symbols"](enabled)


def test_the_resolver_loads_strategies_when_the_module_cache_is_empty():
    """`_initialize_live_risk_authority` runs BEFORE the module-level
    `load_strategies_from_db()`, so `_cached_strategies` is still None there. A
    resolver that trusted it would create the row at 10/20/10% and block every
    strategy_eb buy until some later refresh widened it."""
    ns = _extract("_live_risk_limits_for_this_document")
    calls = []

    def _load():
        calls.append(1)
        return ([{"strategy": "strategy_eb",
                  "config": {"strategy_eb_enabled": True}}], None, None)

    ns["_cached_strategies"] = None
    ns["load_strategies_from_db"] = _load
    assert ns["_live_risk_limits_for_this_document"]() == EB
    assert calls == [1]


def test_the_resolver_prefers_a_populated_cache_over_a_query():
    ns = _extract("_live_risk_limits_for_this_document")

    def _load():
        raise AssertionError("the loaded cache must be used as-is")

    ns["_cached_strategies"] = [{"strategy": "strategy_eb",
                                 "config": {"strategy_eb_enabled": True}}]
    ns["load_strategies_from_db"] = _load
    assert ns["_live_risk_limits_for_this_document"]() == EB


def test_the_resolver_falls_back_to_the_defaults_when_the_load_fails():
    ns = _extract("_live_risk_limits_for_this_document")

    def _load():
        raise RuntimeError("database is unreachable")

    ns["_cached_strategies"] = None
    ns["load_strategies_from_db"] = _load
    assert ns["_live_risk_limits_for_this_document"]() is DEFAULT_RISK_LIMITS


def _function_source(name):
    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in broker.py")


@pytest.mark.parametrize("func", ["_initialize_live_risk_authority",
                                  "_refresh_live_account_risk_state"])
def test_the_live_risk_path_never_reads_the_module_strategy_cache(func):
    """The ordering sentinel. `_cached_strategies` is assigned at the bottom of
    broker.py, after the MODE_LIVE branch has already bootstrapped risk state, so
    reading it on this path is only ever correct by accident."""
    node = _function_source(func)
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    assert "_cached_strategies" not in names
    called = {n.func.id for n in ast.walk(node)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_live_risk_limits_for_this_document" in called


def test_the_live_trim_site_honours_the_eb_position_cap_opt_in():
    """2026-08-31 paper tick: GLD $5,384 trimmed to $1,615 — the 0.95 cap the
    backtest engine honours was invisible live. The live trim site must read
    the same opt-in."""
    fns = _extract("_strategy_eb_single_position_pct")
    fn = fns["_strategy_eb_single_position_pct"]
    lane = lambda **cfg: [{"strategy": "strategy_eb", "config": {
        "strategy_eb_enabled": True, "honour_single_position_cap": True,
        "broker_max_single_position_pct": 0.95, **cfg}}]
    assert fn(lane()) == 0.95
    assert fn(lane(strategy_eb_enabled="false")) is None
    assert fn(lane(honour_single_position_cap=False)) is None
    assert fn(lane(broker_max_single_position_pct=True)) is None
    assert fn(lane(broker_max_single_position_pct=1.5)) is None
    assert fn(None) is None
    src = open(os.path.join(_backend, "broker.py")).read()
    assert src.count("_strategy_eb_single_position_pct(_cached_strategies)") >= 1


def test_the_outlier_lane_widens_the_symbol_fraction_and_the_cap():
    ns = _extract("_strategy_eb_risk_limits", "_strategy_eb_single_position_pct")
    specs = [{"strategy": "strategy_eb", "config": {
                  "strategy_eb_enabled": True, "honour_single_position_cap": True,
                  "broker_max_single_position_pct": 0.95}},
             {"strategy": "outlier_sleeve", "config": {
                  "outlier_sleeve_enabled": True, "honour_single_position_cap": True,
                  "broker_max_single_position_pct": 0.95}}]
    limits = ns["_strategy_eb_risk_limits"](specs)
    assert limits.max_symbol_fraction == Decimal("0.7")   # EB's 0.7 is wider than the sleeve's 0.35
    assert limits.max_order_fraction == Decimal("0.7") and limits.kill == Decimal("0.45")
    assert ns["_strategy_eb_single_position_pct"](specs) == 0.95
    only_sleeve = [specs[1]]
    assert ns["_strategy_eb_risk_limits"](only_sleeve).max_symbol_fraction == Decimal("0.35")
    assert ns["_strategy_eb_single_position_pct"](only_sleeve) == 0.95
    disabled = [{"strategy": "outlier_sleeve", "config": {
                     "outlier_sleeve_enabled": False, "honour_single_position_cap": True,
                     "broker_max_single_position_pct": 0.95}}]
    assert ns["_strategy_eb_risk_limits"](disabled) is None
    assert ns["_strategy_eb_single_position_pct"](disabled) is None


def test_a_malformed_sibling_lane_does_not_drop_the_eb_envelope():
    """2026-09-03: a sleeve lane whose limits are refused at construction used
    to escape to the outer except and return None for the WHOLE document —
    strategy_eb's envelope discarded, the module defaults installed, every EB
    buy blocked on max_order_notional. The bad lane is skipped; EB stands.
    Order must not matter."""
    ns = _extract("_strategy_eb_risk_limits")
    eb = {"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}
    bad = {"strategy": "outlier_sleeve",
           "config": {"outlier_sleeve_enabled": True, "live_soft_drawdown": 0.9}}
    junk = {"strategy": "outlier_sleeve",
            "config": {"outlier_sleeve_enabled": True, "live_max_order_fraction": "junk"}}
    assert ns["_strategy_eb_risk_limits"]([eb, bad]) == EB
    assert ns["_strategy_eb_risk_limits"]([bad, eb]) == EB
    assert ns["_strategy_eb_risk_limits"]([junk, eb, bad]) == EB
