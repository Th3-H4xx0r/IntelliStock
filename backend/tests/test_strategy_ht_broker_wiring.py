"""Strategy HT is registered for live ticks and declares its own envelope.

Unregistered, a live HT tick keeps the broker's 15% single-position failsafe
and the module risk defaults, and every 100%-of-NAV TQQQ buy is trimmed to
$0.00 (BT102936; how Strategy XS first shipped inert). Registered without a
defaults_by_lane row, the envelope lookup KeyErrors the WHOLE document (D5).
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from live_risk_state import RiskLimits  # noqa: E402
from strategy_ht import DEFAULTS  # noqa: E402
from swing_broker_harness import extract, module_assign  # noqa: E402

HT = {"strategy": "strategy_ht", "config": {**DEFAULTS, "strategy_ht_enabled": True}}
HT_OFF = {"strategy": "strategy_ht", "config": {**DEFAULTS, "strategy_ht_enabled": False}}
EB_DOC_200 = [{"strategy": "strategy_eb",
               "config": {"strategy_eb_enabled": True, "honour_single_position_cap": True,
                          "broker_max_single_position_pct": 0.95}}]
EB_DOC_200_ENVELOPE = RiskLimits(max_order_fraction="0.70", max_symbol_fraction="0.70",
                                 max_leveraged_fraction="0.70", soft="0.25", hard="0.35", kill="0.45")


def _limits():
    return extract(("_strategy_eb_risk_limits", "_strategy_eb_single_position_pct",
                    "_truthy", "_merged_strategy_settings"),
                   assigns=("_LANE_ENABLE_FLAGS",),
                   namespace={"_log": lambda *a, **k: None,
                              "_live_risk_limits_last_reason": "",
                              "_core_sleeve_cfg_raw": lambda specs: {}})


def test_the_ht_lane_is_registered_under_both_spellings():
    flags = module_assign("_LANE_ENABLE_FLAGS")
    assert flags["strategy_ht"] == flags["strategyht"] == "strategy_ht_enabled"


def _universe_fns():
    return extract(("_strategy_ht_universe_symbols", "_strategy_ht_required_symbols",
                    "_truthy", "_merged_strategy_settings"))


PAPER = {"strategy": "strategy_ht", "weight": 1.0,
         "config": {**DEFAULTS, "strategy_ht_enabled": True, "filter_symbol": "QQQ",
                    "one_x_symbol": "QQQ", "in_symbol": "TQQQ", "out_symbols": {"BIL": 1.0}}}


def test_the_declared_ht_universe_is_fetched():
    """Missing the fetch site is SILENT: with no filter bars the lane refuses
    with 'insufficient history' on every tick and the document holds forever
    (observed on strategy-ht-paper's first live tick, 2026-11-09)."""
    ns = _universe_fns()
    assert ns["_strategy_ht_universe_symbols"]([PAPER]) == ["BIL", "QQQ", "TQQQ"]
    assert ns["_strategy_ht_universe_symbols"]([HT]) == ["BIL", "SPY", "SSO"]
    assert ns["_strategy_ht_required_symbols"]([PAPER]) == ["QQQ", "TQQQ"]


def test_a_disabled_or_absent_ht_lane_declares_no_symbols():
    ns = _universe_fns()
    for fn in ("_strategy_ht_universe_symbols", "_strategy_ht_required_symbols"):
        assert ns[fn]([HT_OFF]) == []
        assert ns[fn]([{"strategy": "strategy_ht", "config": {"strategy_ht_enabled": "false"}}]) == []
        assert ns[fn]([{"strategy": "graph_nexus_analysis", "config": {}}]) == []
        for junk in (None, [], [None], ["strategy_ht"], [{"strategy": None}]):
            assert ns[fn](junk) == [], junk


def test_both_wiring_points_reference_the_ht_universe():
    """The backtest fetch loop and the live equity-bars block must both call the
    fetch site, and the live block must add HT's required series."""
    from swing_broker_harness import source
    src = source()
    assert "for _ht_sym in _strategy_ht_universe_symbols(_cached_strategies)" in src
    assert "_ht_syms = _strategy_ht_universe_symbols(" in src
    assert "_strategy_ht_required_symbols(" in src.split("required=list(dict.fromkeys(", 1)[1][:400]


def test_the_ht_lane_declares_its_live_envelope():
    limits = _limits()["_strategy_eb_risk_limits"]([HT])
    assert limits == RiskLimits(max_order_fraction=1.0, max_symbol_fraction=1.0,
                                max_leveraged_fraction=1.0, soft=0.35, hard=0.50, kill=0.60)


def test_the_ht_single_position_cap_is_honoured_live():
    assert _limits()["_strategy_eb_single_position_pct"]([HT]) == 1.0


def test_a_disabled_ht_lane_declares_nothing():
    ns = _limits()
    assert ns["_strategy_eb_risk_limits"]([HT_OFF]) is None
    assert ns["_strategy_eb_single_position_pct"]([HT_OFF]) is None


def test_the_eb_document_is_unchanged_by_an_ht_sibling():
    """The widest envelope wins across ENABLED lanes; a disabled HT sibling must
    leave doc 200's envelope and cap exactly as they are."""
    ns = _limits()
    assert ns["_strategy_eb_risk_limits"](EB_DOC_200 + [HT_OFF]) == EB_DOC_200_ENVELOPE
    assert ns["_strategy_eb_single_position_pct"](EB_DOC_200 + [HT_OFF]) == 0.95


def test_the_defaults_row_exists_for_both_spellings():
    import ast
    from swing_broker_harness import tree
    fn = next(n for n in tree().body if isinstance(n, ast.FunctionDef)
              and n.name == "_strategy_eb_risk_limits")
    node = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "defaults_by_lane" for t in n.targets))
    mapped = {k.value for k in node.value.keys}
    assert {"strategy_ht", "strategyht"} <= mapped


def test_the_envelope_values_validate_as_risk_limits():
    RiskLimits(max_order_fraction=DEFAULTS["live_max_order_fraction"],
               max_symbol_fraction=DEFAULTS["live_max_symbol_fraction"],
               max_leveraged_fraction=DEFAULTS["live_max_leveraged_fraction"],
               soft=DEFAULTS["live_soft_drawdown"], hard=DEFAULTS["live_hard_drawdown"],
               kill=DEFAULTS["live_kill_drawdown"])
