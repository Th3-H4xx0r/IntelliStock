import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.observers import (
    funnel_summary, observations_from_backtest, observations_from_live,
)


def _doc():
    """Shape copied from broker.py:17342 (decisions) and
    portfolio_emulator.py:229 (trades). Note symbol vs ticker."""
    return {
        "id": 559934,
        "backtest_decisions": [
            {"timestamp": "2026-04-01T13:30:00", "symbol": "INTC", "action": "buy",
             "decision": 1, "normalized_score": 1.0, "primary_strategy": "graph_nexus_analysis",
             "final_reason": "graph", "strategies": [
                 {"strategy": "graph_nexus_analysis", "decision": 1, "weight": 0.5,
                  "reason": "graph"}]},
            {"timestamp": "2026-04-01T13:30:00", "symbol": "XOM", "action": "buy",
             "decision": 1, "normalized_score": 1.0, "primary_strategy": "graph_nexus_analysis",
             "final_reason": "graph", "strategies": []},
            {"timestamp": "2026-04-02T13:30:00", "symbol": "INTC", "action": "hold",
             "decision": 0, "normalized_score": 1.0, "primary_strategy": "rsi",
             "final_reason": "", "strategies": []},
        ],
        "backtest_trades": [
            {"timestamp": "2026-04-01T13:30:00", "action": "buy", "ticker": "INTC",
             "shares": 10.0, "price": 22.5, "total": 225.0, "cash_after": 5000.0},
        ],
    }


def test_every_decision_becomes_an_observation():
    obs = observations_from_backtest(_doc())
    assert len(obs) == 3


def test_the_filled_name_is_marked_executed_despite_symbol_vs_ticker():
    intc = [o for o in observations_from_backtest(_doc())
            if o.symbol == "INTC" and o.as_of == "2026-04-01T13:30:00"][0]
    assert intc.executed is True
    assert intc.refusal_reason is None


def test_a_buy_that_never_filled_is_a_refusal():
    xom = [o for o in observations_from_backtest(_doc()) if o.symbol == "XOM"][0]
    assert xom.executed is False
    # Phase 1 records THAT it was refused; WHY lives only in run logs.
    assert xom.refusal_reason == "unknown"


def test_a_hold_is_not_a_refusal():
    hold = [o for o in observations_from_backtest(_doc()) if o.decision == 0][0]
    assert hold.executed is False
    assert hold.refusal_reason is None


def test_strategy_id_comes_from_the_primary_strategy_not_a_hardcoded_name():
    ids = {o.strategy_id for o in observations_from_backtest(_doc())}
    assert ids == {"graph_nexus_analysis", "rsi"}


def test_votes_are_carried_through():
    intc = [o for o in observations_from_backtest(_doc())
            if o.symbol == "INTC" and o.decision == 1][0]
    assert intc.votes == (("graph_nexus_analysis", 1, 0.5),)


def test_funnel_summary_counts_the_refusals():
    assert funnel_summary(_doc()) == {
        "decided": 3, "executed": 1, "refused": 1,
        "buy_decided": 2, "buy_executed": 1,
    }


def test_a_document_with_no_decisions_yields_nothing_and_does_not_raise():
    assert observations_from_backtest({"id": 1}) == []
    assert funnel_summary({"id": 1})["decided"] == 0


def _live_rows():
    """BotTradeDecisions shape — written by broker.py via
    bot_decision_log.primary_decision()."""
    return [
        {"id": "a", "brokerage_id": "alpaca-main", "symbol": "MSFT",
         "ts": "2026-08-14T17:02:00Z", "decision": 1, "action": "buy",
         "primary_strategy": "graph_nexus_analysis", "normalized_score": 1.0,
         "filled": True,
         "strategy_summary": [{"strategy": "graph_nexus_analysis",
                               "decision": 1, "weight": 0.5, "reason": "r"}]},
        {"id": "b", "brokerage_id": "alpaca-main", "symbol": "HAPN",
         "ts": "2026-08-14T17:02:00Z", "decision": 1, "action": "buy",
         "primary_strategy": "graph_nexus_analysis", "normalized_score": 1.0,
         "filled": False, "strategy_summary": []},
    ]


def test_live_rows_become_observations_with_live_origin():
    obs = observations_from_live(_live_rows(), instance_id="alpaca-main")
    assert len(obs) == 2
    assert {o.origin for o in obs} == {"live"}
    assert {o.run_id for o in obs} == {"alpaca-main"}


def test_an_unfilled_live_buy_is_a_refusal():
    obs = observations_from_live(_live_rows(), instance_id="alpaca-main")
    hapn = [o for o in obs if o.symbol == "HAPN"][0]
    assert hapn.executed is False and hapn.refusal_reason == "unknown"


def test_live_and_backtest_observations_never_collide_on_id():
    live = observations_from_live(_live_rows(), instance_id="559934")[0]
    bt = observations_from_backtest(_doc())[0]
    assert live.id != bt.id
