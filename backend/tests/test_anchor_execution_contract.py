"""Execution contract for default-OFF anchor reinforcement.

The historical defect crossed three real layers: the planner allocated, broker
risk gates rejected, and stage state nevertheless advanced. These tests drive
the real planner, AST-extracted broker helpers (broker.py is not import-safe),
and a real PortfolioEmulator next-event fill. A planner line alone never passes.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import types

import pytest

from backend.portfolio_emulator import PortfolioEmulator
from backend.simulated_execution import (
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationQuote,
)
from backend.strategies import graph_nexus_analysis as graph

NAV = 6000.0
T0 = datetime(2026, 4, 16, 14, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
ZERO = ExecutionCostModel(
    version="anchor-test-zero", spread_bps=0.0, slippage_bps=0.0,
    fee_bps=0.0, latency=timedelta(0),
)


def _candidate(**overrides):
    value = {
        "ticker": "WIN", "is_equity": True, "held_days": 8,
        "unrealized_pct": 15.0, "drop_from_peak_pct": 0.0,
        "entry_notional": 840.0, "raw_net_score": 1.2,
        "position_value": 966.0,
    }
    value.update(overrides)
    return value


def _cfg(**overrides):
    value = {
        "anchor_reinforce_enabled": True,
        "anchor_reinforce_target_pct": 20,
        "anchor_reinforce_stage1_days": 7,
        "anchor_reinforce_stage1_pnl": 15,
        "anchor_reinforce_stage1_mult": 1.3,
        "anchor_reinforce_stage2_days": 14,
        "anchor_reinforce_stage2_pnl": 30,
        "anchor_reinforce_stage2_mult": 1.6,
        "anchor_reinforce_stage3_days": 21,
        "anchor_reinforce_stage3_pnl": 50,
        "anchor_reinforce_stage3_mult": 2.0,
        "anchor_reinforce_max_drawdown_from_peak_pct": 5.0,
        "anchor_reinforce_execution_enabled": True,
        "anchor_reinforce_execution_max_position_pct": 20,
        "anchor_reinforce_execution_turnover_ceiling_pct": 0.80,
        "anchor_reinforce_execution_core_floor_enabled": True,
        "backtest_credit_pending_sell_proceeds": True,
        "single_position_max_pct": 25,
        "min_position_size": 100,
    }
    value.update(overrides)
    return value


def _plan(candidate=None, cfg=None, cache=None, budget=1000.0):
    return graph._plan_anchor_reinforcement(
        [candidate or _candidate()], budget, min_position_size=100,
        config=cfg or _cfg(), strategy_cache=cache if cache is not None else {},
        portfolio_total=NAV,
    )


def test_absent_or_false_execution_flag_keeps_legacy_plan_time_commit():
    for cfg in (_cfg(anchor_reinforce_execution_enabled=False),
                {k: v for k, v in _cfg().items()
                 if k != "anchor_reinforce_execution_enabled"}):
        cache = {}
        funded, _ = _plan(cfg=cfg, cache=cache)
        assert funded[0]["buy_cash"] == pytest.approx(234.0)
        assert cache["_anchor_reinforce_stage"] == {"WIN": 1}
        assert "_anchor_reinforce_pending" not in cache
        assert "anchor_execution_enabled" not in funded[0]


def test_absent_or_false_does_not_emit_execution_only_hint_keys():
    block = graph.__file__ and Path(graph.__file__).read_text()
    start = block.index('nexus_position_sizes[_add["ticker"]] = {')
    update = block.index('if bool(_add.get("anchor_execution_enabled", False)):', start)
    legacy_payload = block[start:update]
    assert '"anchor_reinforcement"' not in legacy_payload
    assert '"anchor_plan_id"' not in legacy_payload


def test_safe_mode_plans_pending_and_does_not_commit_before_fill():
    cache = {}
    funded, remaining = _plan(cache=cache, budget=150.0)
    assert funded[0]["buy_cash"] == pytest.approx(150.0)
    assert remaining == 0.0
    assert cache["_anchor_reinforce_stage"] == {}
    assert cache["_anchor_reinforce_pending"]["WIN"]["stage"] == 1
    # Pending suppresses a duplicate plan and its second budget charge.
    second, second_remaining = _plan(cache=cache, budget=150.0)
    assert second == [] and second_remaining == 150.0


def test_safe_mode_uses_actual_position_value_and_lane_cap():
    cache = {}
    candidate = _candidate(
        held_days=15, unrealized_pct=30.0, position_value=1100.0)
    funded, _ = _plan(candidate=candidate, cache=cache)
    # Stage-2 raw target is >20%, but the explicit lane cap holds it at $1200.
    assert funded[0]["anchor_stage"] == 2
    assert funded[0]["anchor_target_total"] == pytest.approx(1200.0)
    assert funded[0]["anchor_current_value"] == pytest.approx(1100.0)
    assert funded[0]["buy_cash"] == pytest.approx(100.0)


def test_safe_mode_fails_closed_without_valid_explicit_cap():
    for cap in (0, -1, "bad"):
        funded, remaining = _plan(
            cfg=_cfg(anchor_reinforce_execution_max_position_pct=cap),
            cache={}, budget=500.0)
        assert funded == [] and remaining == 500.0


# broker.py is intentionally not import-safe. Extract its real functions.
BROKER = Path(__file__).resolve().parents[1] / "broker.py"
SOURCE = BROKER.read_text()
TREE = ast.parse(SOURCE)
WANTED = {
    "_core_sleeve_cfg_raw",
    "_anchor_reinforcement_execution_policy",
    "_anchor_reinforcement_position_headroom",
    "_anchor_reinforcement_turnover_allows",
    "_anchor_reinforcement_block",
    "_reconcile_anchor_pending_orders",
    "_exec_fundable_amount",
    "_apply_backtest_confirmed_fill_state",
}
LOGS = []
NS = {
    "MODE_BACKTEST": "backtest",
    "_strategy_cache": {},
    "_cached_strategies": [],
    "_log": lambda message, *_args, **_kwargs: LOGS.append(str(message)),
    "portfolio_emulator": None,
}
for node in TREE.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANTED:
        exec(compile(ast.Module(body=[node], type_ignores=[]), "broker.py", "exec"), NS)
for name in WANTED:
    assert name in NS, f"failed to extract {name}"
broker = types.SimpleNamespace(**NS)


def _spec(cfg=None):
    return [{"strategy": "graph_nexus_analysis", "config": cfg or _cfg()}]


def test_policy_is_double_gated_backtest_only_and_bounded():
    hint = {"anchor_reinforcement": True, "anchor_stage": 1}
    assert broker._anchor_reinforcement_execution_policy(
        _spec(_cfg(anchor_reinforce_execution_enabled=False)), hint, "backtest") is None
    assert broker._anchor_reinforcement_execution_policy(
        _spec(), {}, "backtest") is None
    policy = broker._anchor_reinforcement_execution_policy(
        _spec(), hint, "backtest")
    assert policy == {
        "research_only": False,
        "valid": True,
        "max_position_fraction": 0.20,
        "turnover_ceiling": 0.80,
        "allow_core_floor": True,
        "min_fill": 100.0,
    }
    assert broker._anchor_reinforcement_execution_policy(
        _spec(), hint, "live")["research_only"] is True
    invalid = broker._anchor_reinforcement_execution_policy(
        _spec(_cfg(anchor_reinforce_execution_turnover_ceiling_pct=0)),
        hint, "backtest")
    assert invalid["valid"] is False
    bounded = broker._anchor_reinforcement_execution_policy(
        _spec(_cfg(anchor_reinforce_execution_turnover_ceiling_pct=1.25)),
        hint, "backtest")
    assert bounded["turnover_ceiling"] == pytest.approx(1.0)
    no_credit = broker._anchor_reinforcement_execution_policy(
        _spec(_cfg(backtest_credit_pending_sell_proceeds=False)),
        hint, "backtest")
    assert no_credit["valid"] is False


def test_lane_position_headroom_uses_actual_shares_and_never_exceeds_25pct():
    class Book:
        _positions = {"WIN": 10.0}
        def get_portfolio_value(self, _prices):
            return NAV
    policy = broker._anchor_reinforcement_execution_policy(
        _spec(_cfg(anchor_reinforce_execution_max_position_pct=40)),
        {"anchor_reinforcement": True}, "backtest")
    # Strategy cap 25% wins over requested 40%: $1500 - $1000 = $500.
    assert policy["max_position_fraction"] == pytest.approx(0.25)
    assert broker._anchor_reinforcement_position_headroom(
        policy, Book(), {"WIN": 100.0}, "WIN", 100.0) == pytest.approx(500.0)


def test_lane_turnover_ceiling_counts_the_planned_add_and_is_authoritative():
    policy = broker._anchor_reinforcement_execution_policy(
        _spec(), {"anchor_reinforcement": True}, "backtest")
    allowed, projected = broker._anchor_reinforcement_turnover_allows(
        policy, 0.75, 250.0, NAV)
    assert allowed is True and projected == pytest.approx(0.7916667)
    allowed, projected = broker._anchor_reinforcement_turnover_allows(
        policy, 0.79, 250.0, NAV)
    assert allowed is False and projected > 0.80
    # An invalid/missing policy can never inherit the general raw-score bypass.
    assert broker._anchor_reinforcement_turnover_allows(
        None, 0.0, 1.0, NAV)[0] is False


def test_execution_wiring_enforces_lane_ceiling_before_global_budget_binds():
    # The explicit anchor ceiling must be evaluated on every anchor order, not
    # only as a bypass after the general turnover budget has already bound.
    buy_gate = SOURCE[SOURCE.index("_tb_conv_min ="):SOURCE.index(
        "_rc = _regime_position_cap_hard", SOURCE.index("_tb_conv_min ="))]
    assert "if _anchor_policy:" in buy_gate
    assert "if _turnover_blocked and _anchor_policy:" not in buy_gate
    assert "_turnover_ledger_rolling(current_time)" in buy_gate
    assert "ANCHOR TURNOVER BLOCK:" in buy_gate
    assert 'symbol, "turnover_ceiling"' in buy_gate


def test_execution_plan_does_not_crowd_unrelated_new_entry_budget():
    source = Path(graph.__file__).read_text()
    budget = source[source.index("_winner_add_spent = ("):source.index(
        "for _add in _winner_add_funded:", source.index("_winner_add_spent = ("))]
    assert "0.0" in budget and "if _anchor_exec_mode" in budget
    assert "_stock_budget_after_adds = _stock_budget_available - _winner_add_spent" in budget


def test_final_nonbuy_decision_resolves_pending_anchor_intent():
    decision_gate = SOURCE[SOURCE.index("_anchor_final_hint ="):SOURCE.index(
        "# Execute buy/sell via PortfolioEmulator", SOURCE.index("_anchor_final_hint ="))]
    assert "decision != 1" in decision_gate
    assert 'symbol, "final_decision_not_buy"' in decision_gate


def test_invalid_or_research_only_anchor_cannot_release_core_funding():
    funding = SOURCE[SOURCE.index("_fr_anchor_policy ="):SOURCE.index(
        "_core_funding_request = _fr_capped", SOURCE.index("_fr_anchor_policy ="))]
    rejection = funding.index('_fr_anchor_policy.get("research_only")')
    accounting = funding.index("if _fr_is_conv:")
    assert rejection < accounting
    assert 'or not _fr_anchor_policy.get("valid")' in funding
    assert "continue" in funding[rejection:accounting]
    # A valid-but-over-ceiling order is also excluded before core accounting,
    # preventing a same-bar core release/redeploy round trip.
    assert "_anchor_reinforcement_turnover_allows(" in funding[:accounting]
    assert "_anchor_reinforcement_position_headroom(" in funding[:accounting]
    assert "_fr_anchor_reserved_turnover += 2.0" in funding[:accounting]
    assert "not releasing core for a buy the " in funding[:accounting]


def test_broker_block_clears_pending_without_committing_stage():
    LOGS.clear()
    NS["_strategy_cache"] = {
        "graph_nexus_analysis": {
            "_anchor_reinforce_stage": {},
            "_anchor_reinforce_pending": {
                "WIN": {"stage": 1, "planned": 234.0}
            },
        }
    }
    broker._anchor_reinforcement_block(
        "WIN", "satellite_cap", {"anchor_stage": 1}, detail="room=$-595")
    cache = NS["_strategy_cache"]["graph_nexus_analysis"]
    assert cache["_anchor_reinforce_pending"] == {}
    assert cache["_anchor_reinforce_stage"] == {}
    assert any(
        "ANCHOR BLOCK: WIN stage=1 gate=satellite_cap" in line
        for line in LOGS)


def _configured_emulator():
    emu = PortfolioEmulator(
        NAV, execution_simulator=NextEventExecutionSimulator(ZERO),
        execution_delay=timedelta(hours=1), equity_cost_model=ZERO)
    assert emu.buy("WIN", 10.0, 100.0, timestamp=T0 - timedelta(days=8))
    return emu


def _install_fill_globals(emu, pending):
    LOGS.clear()
    NS["portfolio_emulator"] = emu
    NS["_cached_strategies"] = _spec()
    record = dict(pending)
    record.setdefault("plan_id", "WIN:s1:p1")
    record.setdefault("order_id", None)
    NS["_strategy_cache"] = {
        "graph_nexus_analysis": {
            "_anchor_reinforce_stage": {},
            "_anchor_reinforce_filled": {
                "WIN": {"stage": record["stage"],
                        "filled_notional": record.get("filled_notional", 0.0)}
            },
            "_anchor_reinforce_pending": {"WIN": record},
        }
    }


def _bind_order(receipt):
    pending = NS["_strategy_cache"]["graph_nexus_analysis"][
        "_anchor_reinforce_pending"]["WIN"]
    pending["order_id"] = receipt.order_id


def test_real_next_event_anchor_fill_commits_stage_and_quantity():
    emu = _configured_emulator()
    _install_fill_globals(emu, {
        "stage": 1, "planned": 200.0, "target_total": 1200.0,
        "current_value": 1000.0, "filled_notional": 0.0,
    })
    receipt = emu.execute_signal(
        "WIN", 1, 100.0, timestamp=T0, cash_per_trade=200.0,
        order_source="anchor_reinforcement:stage=1:plan=WIN:s1:p1")
    assert receipt.accepted and not receipt.filled
    _bind_order(receipt)
    assert emu.get_positions()["WIN"] == pytest.approx(10.0)
    fills = emu.process_quote(SimulationQuote.from_mid(
        symbol="WIN", timestamp=T1, mid=100.0, spread_bps=0.0))
    assert len(fills) == 1 and fills[0].source.startswith("anchor_reinforcement:")
    broker._apply_backtest_confirmed_fill_state(fills[0])
    assert emu.get_positions()["WIN"] == pytest.approx(12.0)
    cache = NS["_strategy_cache"]["graph_nexus_analysis"]
    assert cache["_anchor_reinforce_stage"] == {"WIN": 1}
    assert cache["_anchor_reinforce_pending"] == {}
    assert any("ANCHOR FILL: WIN" in line for line in LOGS)
    assert any("ANCHOR STAGE COMMIT: WIN" in line for line in LOGS)


def test_partial_final_fill_below_target_does_not_complete_stage():
    emu = _configured_emulator()
    _install_fill_globals(emu, {
        "stage": 1, "planned": 100.0, "target_total": 1400.0,
        "current_value": 1000.0, "filled_notional": 0.0,
    })
    receipt = emu.execute_signal(
        "WIN", 1, 100.0, timestamp=T0, cash_per_trade=100.0,
        order_source="anchor_reinforcement:stage=1:plan=WIN:s1:p1")
    _bind_order(receipt)
    fill = emu.process_quote(SimulationQuote.from_mid(
        symbol="WIN", timestamp=T1, mid=100.0, spread_bps=0.0))[0]
    broker._apply_backtest_confirmed_fill_state(fill)
    cache = NS["_strategy_cache"]["graph_nexus_analysis"]
    assert cache["_anchor_reinforce_stage"] == {}
    assert cache["_anchor_reinforce_pending"] == {}
    assert any("ANCHOR STAGE PARTIAL: WIN" in line for line in LOGS)


def test_final_partial_with_subminimum_residual_does_not_commit():
    emu = _configured_emulator()
    _install_fill_globals(emu, {
        "stage": 1, "planned": 150.0, "required_notional": 234.0,
        "target_total": 1234.0, "current_value": 1000.0,
        "filled_notional": 0.0,
    })
    receipt = emu.execute_signal(
        "WIN", 1, 100.0, timestamp=T0, cash_per_trade=150.0,
        order_source="anchor_reinforcement:stage=1:plan=WIN:s1:p1")
    _bind_order(receipt)
    fill = emu.process_quote(SimulationQuote.from_mid(
        symbol="WIN", timestamp=T1, mid=100.0, spread_bps=0.0))[0]
    broker._apply_backtest_confirmed_fill_state(fill)
    cache = NS["_strategy_cache"]["graph_nexus_analysis"]
    assert cache["_anchor_reinforce_stage"] == {}
    assert cache["_anchor_reinforce_filled"]["WIN"]["filled_notional"] == pytest.approx(150.0)
    assert any("remaining=$84.00" in line and "STAGE PARTIAL" in line for line in LOGS)


def test_fill_uses_mid_mark_and_rejects_stage_or_order_mismatch():
    emu = _configured_emulator()
    _install_fill_globals(emu, {
        "stage": 1, "planned": 100.0, "target_total": 1200.0,
        "current_value": 1000.0, "filled_notional": 0.0,
    })
    receipt = emu.execute_signal(
        "WIN", 1, 100.0, timestamp=T0, cash_per_trade=100.0,
        order_source="anchor_reinforcement:stage=1:plan=WIN:s1:p1")
    _bind_order(receipt)
    fill = emu.process_quote(SimulationQuote(
        symbol="WIN", timestamp=T1, bid=90.0, ask=110.0))[0]
    broker._apply_backtest_confirmed_fill_state(fill)
    cache = NS["_strategy_cache"]["graph_nexus_analysis"]
    assert cache["_anchor_reinforce_stage"] == {}
    assert any("mark=$100.000000" in line for line in LOGS)

    emu2 = _configured_emulator()
    _install_fill_globals(emu2, {
        "stage": 2, "plan_id": "WIN:s2:p1", "planned": 100.0,
        "target_total": 1100.0, "current_value": 1000.0,
        "filled_notional": 0.0,
    })
    bad = emu2.execute_signal(
        "WIN", 1, 100.0, timestamp=T0, cash_per_trade=100.0,
        order_source="anchor_reinforcement:stage=1:plan=WIN:s1:p1")
    _bind_order(bad)
    fill2 = emu2.process_quote(SimulationQuote.from_mid(
        symbol="WIN", timestamp=T1, mid=100.0, spread_bps=0.0))[0]
    broker._apply_backtest_confirmed_fill_state(fill2)
    assert NS["_strategy_cache"]["graph_nexus_analysis"]["_anchor_reinforce_stage"] == {}
    assert any("ANCHOR FILL MISMATCH" in line for line in LOGS)


def test_confirmed_full_exit_resets_position_episode_state():
    emu = _configured_emulator()
    NS["portfolio_emulator"] = emu
    NS["_cached_strategies"] = _spec()
    NS["_strategy_cache"] = {
        "graph_nexus_analysis": {
            "_anchor_reinforce_stage": {"WIN": 1},
            "_anchor_reinforce_pending": {},
            "_anchor_reinforce_filled": {"WIN": {"stage": 2, "filled_notional": 50.0}},
        }
    }
    receipt = emu.execute_signal(
        "WIN", -1, 100.0, timestamp=T0, sell_fraction=1.0,
        order_source="main_signal")
    fill = emu.process_quote(SimulationQuote.from_mid(
        symbol="WIN", timestamp=T1, mid=100.0, spread_bps=0.0))[0]
    broker._apply_backtest_confirmed_fill_state(fill)
    cache = NS["_strategy_cache"]["graph_nexus_analysis"]
    assert cache["_anchor_reinforce_stage"] == {}
    assert cache["_anchor_reinforce_filled"] == {}
    assert any("ANCHOR EPISODE RESET: WIN" in line for line in LOGS)


def test_orphaned_order_reconciliation_and_buying_power_are_fail_closed():
    class Sim:
        pending_orders = ()
    class Book:
        _execution_simulator = Sim()
        _execution_cash_reservations = {"other": 150.0}
        def get_buying_power(self, reserved):
            assert reserved == pytest.approx(150.0)
            return 50.0
    NS["_strategy_cache"] = {
        "graph_nexus_analysis": {
            "_anchor_reinforce_stage": {},
            "_anchor_reinforce_pending": {
                "WIN": {"stage": 1, "planned": 100.0,
                        "order_id": "expired-order"}
            },
        }
    }
    broker._reconcile_anchor_pending_orders(Book())
    assert NS["_strategy_cache"]["graph_nexus_analysis"]["_anchor_reinforce_pending"] == {}
    assert broker._exec_fundable_amount(Book(), 200.0) == pytest.approx(50.0)


def test_broker_source_wiring_has_all_greppable_states():
    # Pin the real module-level choke point, not a mirror of its decisions.
    for signature in (
        "ANCHOR BLOCK:", "ANCHOR SATELLITE ADMIT:",
        "ANCHOR TURNOVER ADMIT:", "ANCHOR ORDER:", "ANCHOR FILL:",
        "ANCHOR STAGE COMMIT:", 'order_source=_anchor_order_source',
    ):
        assert signature in SOURCE
