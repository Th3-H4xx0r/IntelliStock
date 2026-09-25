"""swing-port Task 8: the _nexus_option_orders side channel, the per-tick
merge, and registering the swing and wheel lanes."""
import os
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from live_risk_state import RiskLimits
from swing_broker_harness import extract, module_assign, source

DISPATCHER = (
    "run_run_once_strategies", "_residual_sleeve_config",
    "_eb_live_portfolio_view", "_eb_decision_outside_rth",
    "_merged_strategy_settings", "_llm_resolution_is_fatal",
    "_resolve_nexus_runtime_identity", "_run_graph_nexus_with_point_in_time",
)
ORDER = {"signal_id": None, "underlying": "APH",
         "contract": "APH261009P00130000", "option_type": "put",
         "strike": 130.0, "expiry": "2026-10-09",
         "position_intent": "sell_to_open", "qty": 1, "order_type": "limit",
         "limit_price": 1.2, "tif": "day", "reason": "wheel_sto_put"}


def _dispatch(returned, name="strategy_wheel", *, run_mode="backtest",
              emulator=None, seen=None):
    lines = []

    class Lane:
        def run_once(self, *args, **kwargs):
            if seen is not None:
                seen.append(kwargs.get("portfolio_emulator"))
            return dict(returned)

    ns = extract(DISPATCHER, check=("run_run_once_strategies",), namespace={
        "MODE_BACKTEST": "backtest", "MODE_LIVE": "live", "mode": run_mode,
        "os": os, "_strategy_cache": {}, "_strategy_class_cache": {name: Lane},
        "_log": lambda message, color="white": lines.append((message, color)),
        "_load_strategy_class": lambda _name: Lane,
        "_apply_regime_profile": lambda config, regime: dict(config),
        "_apply_live_overrides": lambda config: dict(config),
        "_instance_kind_and_crypto_config": lambda: ("stock", {}),
        "instance_id": "swing-paper", "backtest_row_id": "bt-1",
        "telemetry_llm_call_context": lambda **kwargs: nullcontext(),
        "get_conn": lambda: pytest.fail("model resolution should not run"),
        "resolve_model_refs_in_config": lambda conn, config: config,
        "_partial_trim_syms": lambda sizes: set(),
        "_chop_ret20_cfg": lambda config: None,
    })
    results = ns["run_run_once_strategies"](
        [{"strategy": name, "weight": 1.0, "config": {}}], ["APH"],
        {"APH": 127.0}, datetime(2026, 10, 5, 14, 30, tzinfo=timezone.utc),
        data={},
        portfolio_emulator=emulator if emulator is not None else object(),
        strategy_caches={},
        mode="backtest" if run_mode == "backtest" else None)
    assert results, f"dispatcher swallowed an error: {lines}"
    return results


def test_option_orders_ride_the_metadata_not_the_scores():
    ((_spec, scores, _reasons, metadata),) = _dispatch(
        {"APH": 0, "_nexus_option_orders": [ORDER],
         "_nexus_discovered": ["APH"]})
    assert scores == {"APH": 0}
    assert metadata["_nexus_option_orders"] == [ORDER]


def test_a_lane_without_option_orders_leaves_the_metadata_as_it_was():
    ((_spec, scores, _reasons, metadata),) = _dispatch(
        {"TQQQ": 1, "_nexus_executable_buys": ["TQQQ"]}, name="strategy_eb")
    assert "_nexus_option_orders" not in metadata
    assert metadata == {"_nexus_executable_buys": ["TQQQ"]}


def test_an_empty_option_list_adds_no_metadata_key():
    """EB pin: the key is carried only when a lane emits orders."""
    ((_spec, _scores, _reasons, metadata),) = _dispatch(
        {"APH": 0, "_nexus_option_orders": []})
    assert metadata == {}


def test_the_tick_merges_every_specs_option_orders():
    """A source assertion: the merge lives in the module-level main loop."""
    text = source()
    declared = text.index("nexus_option_orders: list = []")
    merged = text.index(
        'nexus_option_orders.extend(meta.get("_nexus_option_orders") or [])')
    loop = text.index("for _spec_r, _scores_r, _reasons_r, *_meta_r in "
                      "run_once_results:", declared)
    assert declared < loop < merged


# --- G6 re-review: the swing and wheel lanes see the RAW adapter live --------

class _RawAdapter:
    """The live adapter as swing reads it: the positions-stale flag and the
    option-book health are attributes of the adapter object itself."""
    _positions_stale_since = "2026-10-05T14:29:00+00:00"
    _positions = {}

    def option_positions_health(self):
        return {"complete": True, "stale_since": None}


@pytest.mark.parametrize("lane", ["strategy_swing", "strategy_wheel",
                                  "StrategySwing", "strategywheel"])
def test_a_non_eb_lane_receives_the_raw_adapter_live(lane):
    adapter = _RawAdapter()
    seen = []
    _dispatch({"APH": 0}, name=lane, run_mode="live", emulator=adapter,
              seen=seen)
    assert len(seen) == 1
    assert seen[0] is adapter
    assert seen[0]._positions_stale_since == "2026-10-05T14:29:00+00:00"


def test_only_eb_gets_the_pending_order_view_live():
    """The contrast that makes the pin above meaningful: the dispatcher does
    wrap EB's adapter, and only EB's."""
    adapter = _RawAdapter()
    seen = []
    _dispatch({"TQQQ": 0}, name="strategy_eb", run_mode="live",
              emulator=adapter, seen=seen)
    assert len(seen) == 1
    assert seen[0] is not adapter
    assert callable(getattr(seen[0], "pending_execution_symbols", None))


def test_the_swing_and_wheel_lanes_are_registered():
    flags = module_assign("_LANE_ENABLE_FLAGS")
    assert flags["strategy_swing"] == flags["strategyswing"] == \
        "strategy_swing_enabled"
    assert flags["strategy_wheel"] == flags["strategywheel"] == \
        "strategy_wheel_enabled"


def _lanes():
    return extract(("_lane_enabled", "_truthy", "_merged_strategy_settings"),
                   assigns=("_LANE_ENABLE_FLAGS",))


@pytest.mark.parametrize("spec,lane,expected", [
    ({"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}},
     "strategy_wheel", True),
    ({"strategy": "StrategyWheel", "conditions": {"strategy_wheel_enabled": "true"},
      "config": {}}, "strategy_wheel", True),
    ({"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": "false"}},
     "strategy_wheel", False),
    ({"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
     "strategy_wheel", False),
    ({"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}},
     "strategy_swing", False),
    ({"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
     "not_a_lane", False),
])
def test_lane_enabled(spec, lane, expected):
    assert _lanes()["_lane_enabled"]([spec], lane) is expected


def test_lane_enabled_survives_junk():
    fn = _lanes()["_lane_enabled"]
    for junk in (None, [], [None], ["strategy_wheel"], [{"strategy": None}]):
        assert fn(junk, "strategy_wheel") is False


def test_lane_enabled_is_false_for_every_lane_on_an_eb_document():
    """doc 200 carries strategy_eb only: no swing-port branch is entered."""
    fn = _lanes()["_lane_enabled"]
    doc = [{"strategy": "strategy_eb",
            "config": {"strategy_eb_enabled": True,
                       "honour_single_position_cap": True,
                       "broker_max_single_position_pct": 0.95}}]
    assert fn(doc, "strategy_swing") is False
    assert fn(doc, "strategy_wheel") is False


def _limits():
    return extract(
        ("_strategy_eb_risk_limits", "_strategy_eb_single_position_pct",
         "_truthy", "_merged_strategy_settings"),
        assigns=("_LANE_ENABLE_FLAGS", "_live_risk_limits_last_reason"),
        namespace={"_log": lambda *a, **k: None})


SWING = {"strategy": "strategy_swing",
         "config": {"strategy_swing_enabled": True,
                    "honour_single_position_cap": True,
                    "broker_max_single_position_pct": 0.2}}
WHEEL = {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}
EB = {"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}


def test_the_swing_lane_declares_its_live_envelope():
    limits = _limits()["_strategy_eb_risk_limits"]([SWING, WHEEL])
    assert limits == RiskLimits(max_order_fraction=0.2, max_symbol_fraction=0.2,
                                max_leveraged_fraction=0.2, soft=0.25,
                                hard=0.35, kill=0.45)


def test_a_wheel_only_document_keeps_the_module_defaults():
    assert _limits()["_strategy_eb_risk_limits"]([WHEEL]) is None


def test_eb_alone_is_unchanged_by_the_new_rows():
    fn = _limits()["_strategy_eb_risk_limits"]
    disabled_swing = {"strategy": "strategy_swing",
                      "config": {"strategy_swing_enabled": False}}
    assert fn([EB, disabled_swing]) == fn([EB])


def test_the_swing_single_position_cap_is_honoured_live():
    assert _limits()["_strategy_eb_single_position_pct"]([SWING]) == 0.2


# --- EB pins (doc 200, alpaca-main): computed from the pre-change code -------

#: strategy_eb.DEFAULTS' live_* keys on 2026-09-25, before Task 8.
EB_DOC_200_ENVELOPE = RiskLimits(
    max_order_fraction="0.70", max_symbol_fraction="0.70",
    max_leveraged_fraction="0.70", soft="0.25", hard="0.35", kill="0.45")
EB_DOC_200 = [{"strategy": "strategy_eb",
               "config": {"strategy_eb_enabled": True,
                          "honour_single_position_cap": True,
                          "broker_max_single_position_pct": 0.95}}]


@pytest.mark.parametrize("sibling", [
    None,
    {"strategy": "strategy_swing", "config": {"strategy_swing_enabled": False}},
    {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": False}},
    {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}},
    {"strategy": "StrategyWheel", "conditions": {"strategy_wheel_enabled": "1"}},
])
def test_the_eb_document_envelope_and_cap_are_pinned(sibling):
    """Every name added to _LANE_ENABLE_FLAGS needs its defaults_by_lane row,
    or the lookup KeyErrors the WHOLE document's envelope (D5). EB's doc 200
    envelope and single-position cap are exactly what they were."""
    ns = _limits()
    doc = EB_DOC_200 + ([sibling] if sibling is not None else [])
    assert ns["_strategy_eb_risk_limits"](doc) == EB_DOC_200_ENVELOPE
    assert ns["_strategy_eb_single_position_pct"](doc) == 0.95


def test_every_new_lane_name_has_its_defaults_row():
    """The registry and the defaults table move together (ruling 10)."""
    import ast
    from swing_broker_harness import tree
    fn = next(n for n in tree().body if isinstance(n, ast.FunctionDef)
              and n.name == "_strategy_eb_risk_limits")
    node = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "defaults_by_lane"
                        for t in n.targets))
    mapped = {k.value for k in node.value.keys}
    for name in ("strategy_swing", "strategyswing", "strategy_wheel",
                 "strategywheel"):
        assert name in module_assign("_LANE_ENABLE_FLAGS")
        assert name in mapped
