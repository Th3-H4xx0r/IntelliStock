import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.observers import funnel_summary, observations_from_backtest

# NOTE: the live-path tests live in `test_self_learning_join.py`, built from the
# REAL `bot_decision_log.build_decision_doc` shape. The fixture that used to sit
# here invented `decision`/`filled`/`primary_strategy`/`normalized_score`/
# `strategy_summary` — none of which exist on a BotTradeDecisions row — and
# three tests passed against that fiction.


def _doc():
    """Shape copied from broker.py:17342 (decisions) and
    portfolio_emulator.py:229 (trades). Note symbol vs ticker.

    This is the CRYPTO/legacy timestamp shape, where the fill carries the same
    naive stamp as the decision. The equity shape — naive decision, aware-UTC
    fill one execution-delay later — is covered in test_self_learning_join.py.
    """
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
    # "unfilled", not "unknown": this is a decision that reached the execution
    # path and got no fill. Gate refusals never reach the source table at all.
    assert xom.refusal_reason == "unfilled"


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
        "trades_available": 1, "trades_matched": 1,
    }


def test_funnel_summary_reuses_supplied_observations():
    """The pipeline passes them in; recomputing over a 15k-entry list doubles
    the work for nothing."""
    obs = observations_from_backtest(_doc())
    assert funnel_summary(_doc(), obs)["decided"] == 3


def test_a_document_with_no_decisions_yields_nothing_and_does_not_raise():
    assert observations_from_backtest({"id": 1}) == []
    assert funnel_summary({"id": 1})["decided"] == 0


def test_venue_reaches_the_observation_and_its_identity():
    """Two venues are two decision points. Without venue in the identity,
    re-processing a run as crypto silently rewrites the equity row, because
    store.put_observations writes with conflict="update"."""
    equity = observations_from_backtest(_doc(), venue="equity")[0]
    crypto = observations_from_backtest(_doc(), venue="crypto")[0]
    assert equity.venue == "equity" and crypto.venue == "crypto"
    assert equity.id != crypto.id
