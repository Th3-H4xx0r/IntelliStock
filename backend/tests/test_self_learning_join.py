"""The execution join, tested against the timestamp shapes production ACTUALLY
produces — not the idealised ones the first version of this code assumed.

An adversarial sweep found the original join could never match on the equity
path, because it compared `str(timestamp)` on both sides while:

  * the decision stamp is NAIVE  (`broker.py:17343` stamps `current_time`, and
    `_parse_date` at `broker.py:9470` returns a naive datetime), and
  * the trade stamp is AWARE UTC and one execution-delay LATER (the trade row is
    appended by `_apply_confirmed_fill` at `portfolio_emulator.py:1232` as
    `fill.executed_at`, which is `quote.timestamp` — `simulated_execution.py:657`
    — pumped on a later tick).

Every buy therefore read as a refusal, and the subsystem's flagship
`buy_conversion` finding fired on every run as a guaranteed false positive.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.observers import (
    funnel_summary, observations_from_backtest, observations_from_live,
)


def _equity_doc():
    """The REAL equity shape: naive decision stamps, aware-UTC fills one
    15-minute increment later."""
    return {
        "id": 559934,
        "backtest_decisions": [
            {"timestamp": "2026-04-01T13:30:00", "symbol": "INTC",
             "action": "buy", "decision": 1, "normalized_score": 1.0,
             "primary_strategy": "graph_nexus_analysis", "strategies": []},
            {"timestamp": "2026-04-01T13:30:00", "symbol": "XOM",
             "action": "buy", "decision": 1, "normalized_score": 1.0,
             "primary_strategy": "graph_nexus_analysis", "strategies": []},
        ],
        "backtest_trades": [
            {"timestamp": "2026-04-01T13:45:00+00:00", "action": "buy",
             "ticker": "INTC", "shares": 10.0, "price": 22.5},
        ],
    }


def test_a_delayed_aware_utc_fill_matches_its_naive_decision():
    intc = [o for o in observations_from_backtest(_equity_doc())
            if o.symbol == "INTC"][0]
    assert intc.executed is True
    assert intc.refusal_reason is None


def test_the_unfilled_name_is_still_a_refusal():
    xom = [o for o in observations_from_backtest(_equity_doc())
           if o.symbol == "XOM"][0]
    assert xom.executed is False and xom.refusal_reason == "unfilled"


def test_a_fill_before_its_decision_does_not_match():
    """Execution never precedes the decision. A trade earlier in the run must
    not be consumed by a later decision for the same symbol."""
    doc = _equity_doc()
    doc["backtest_trades"] = [
        {"timestamp": "2026-03-30T13:45:00+00:00", "action": "buy",
         "ticker": "INTC", "shares": 10.0, "price": 22.5}]
    intc = [o for o in observations_from_backtest(doc) if o.symbol == "INTC"][0]
    assert intc.executed is False


def test_a_fill_beyond_the_lag_window_does_not_match():
    doc = _equity_doc()
    doc["backtest_trades"] = [
        {"timestamp": "2026-06-01T13:45:00+00:00", "action": "buy",
         "ticker": "INTC", "shares": 10.0, "price": 22.5}]
    intc = [o for o in observations_from_backtest(doc) if o.symbol == "INTC"][0]
    assert intc.executed is False


def test_one_trade_cannot_mark_two_decisions_executed():
    """Consumed-once. Two buys of the same name, one fill — exactly one
    observation may claim it."""
    doc = _equity_doc()
    doc["backtest_decisions"] = [
        {"timestamp": "2026-04-01T13:30:00", "symbol": "INTC", "action": "buy",
         "decision": 1, "normalized_score": 1.0, "primary_strategy": "s",
         "strategies": []},
        {"timestamp": "2026-04-02T13:30:00", "symbol": "INTC", "action": "buy",
         "decision": 1, "normalized_score": 1.0, "primary_strategy": "s",
         "strategies": []},
    ]
    executed = [o for o in observations_from_backtest(doc) if o.executed]
    assert len(executed) == 1
    assert executed[0].as_of == "2026-04-01T13:30:00"


def test_a_sell_fill_cannot_satisfy_a_buy_decision():
    doc = _equity_doc()
    doc["backtest_trades"] = [
        {"timestamp": "2026-04-01T13:45:00+00:00", "action": "sell",
         "ticker": "INTC", "shares": 10.0, "price": 22.5}]
    intc = [o for o in observations_from_backtest(doc) if o.symbol == "INTC"][0]
    assert intc.executed is False


def test_a_dividend_row_is_not_an_execution():
    """`portfolio_emulator.py:976` appends action="dividend" rows into the same
    trade list. A HOLD on the same name must not be marked executed by one."""
    doc = {
        "id": 1,
        "backtest_decisions": [
            {"timestamp": "2026-04-01T13:30:00", "symbol": "SPY",
             "action": "buy", "decision": 1, "normalized_score": 0.5,
             "primary_strategy": "s", "strategies": []}],
        "backtest_trades": [
            {"timestamp": "2026-04-01T13:45:00+00:00", "action": "dividend",
             "ticker": "SPY", "shares": 0.0, "price": 0.0}],
    }
    assert observations_from_backtest(doc)[0].executed is False


def test_naive_and_aware_stamps_on_the_same_bar_still_match():
    """The crypto/legacy path stamps the trade with the SAME naive object as the
    decision. That must keep working."""
    doc = {
        "id": 1,
        "backtest_decisions": [
            {"timestamp": "2026-04-01T13:30:00", "symbol": "BTC/USD",
             "action": "buy", "decision": 1, "normalized_score": 0.5,
             "primary_strategy": "meanrev", "strategies": []}],
        "backtest_trades": [
            {"timestamp": "2026-04-01T13:30:00", "action": "buy",
             "ticker": "BTC/USD", "shares": 1.0, "price": 60000.0}],
    }
    assert observations_from_backtest(doc)[0].executed is True


# ── Join health: the guard that stops a broken join from becoming a finding ──

def test_funnel_summary_reports_join_health():
    summary = funnel_summary(_equity_doc())
    assert summary["trades_available"] == 1
    assert summary["trades_matched"] == 1


def test_a_totally_unmatched_join_is_visible_in_the_summary():
    """If the join breaks again, this is the signal that says so — rather than
    the subsystem reporting a confident 0% conversion rate."""
    doc = _equity_doc()
    doc["backtest_trades"] = [
        {"timestamp": "not-a-timestamp", "action": "buy", "ticker": "INTC",
         "shares": 10.0, "price": 22.5}]
    summary = funnel_summary(doc)
    assert summary["trades_available"] == 1
    assert summary["trades_matched"] == 0


# ── Live path: the REAL BotTradeDecisions shape ──────────────────────────────

def _live_rows():
    """Verbatim from `bot_decision_log.build_decision_doc` (:90-105). The first
    version of this code read `decision`/`filled`/`primary_strategy`/
    `normalized_score`/`strategy_summary` — none of which exist."""
    return [
        {"id": "u1", "instance_id": "alpaca-main", "brokerage_id": "b1",
         "symbol": "MSFT", "side": "buy", "price": 410.2,
         "ts": "2026-08-14T17:02:00+00:00", "created_at": "2026-08-14T17:02:05Z",
         "strategy": "graph_nexus_analysis", "action_intent": "buy",
         "reason": "graph", "score": 1.0, "override_applied": False,
         "contributors": [{"strategy": "graph_nexus_analysis", "decision": 1,
                           "weight": 0.5}]},
        {"id": "u2", "instance_id": "alpaca-main", "brokerage_id": "b1",
         "symbol": "HAPN", "side": "sell", "price": 2.1,
         "ts": "2026-08-14T17:02:00+00:00", "created_at": "2026-08-14T17:02:05Z",
         "strategy": "graph_nexus_analysis", "action_intent": "sell",
         "reason": "", "score": -0.4, "override_applied": False,
         "contributors": []},
    ]


def test_live_side_maps_to_a_real_decision():
    obs = observations_from_live(_live_rows(), instance_id="alpaca-main")
    by_symbol = {o.symbol: o for o in obs}
    assert by_symbol["MSFT"].decision == 1
    assert by_symbol["MSFT"].action == "buy"
    assert by_symbol["HAPN"].decision == -1
    assert by_symbol["HAPN"].action == "sell"


def test_live_score_and_strategy_come_from_the_real_keys():
    msft = observations_from_live(_live_rows(), instance_id="a")[0]
    assert msft.strategy_id == "graph_nexus_analysis"
    assert msft.normalized_score == 1.0


def test_live_contributors_become_votes():
    msft = observations_from_live(_live_rows(), instance_id="a")[0]
    assert msft.votes == (("graph_nexus_analysis", 1, 0.5),)


def test_every_live_row_is_an_execution_not_a_refusal():
    """BotTradeDecisions is written at broker.py:17172 only under
    `if _es_placed and decision in (1, -1)` — it records SUBMITTED orders. A
    live refusal never reaches this table, so claiming one here would be a
    fabrication."""
    obs = observations_from_live(_live_rows(), instance_id="a")
    assert all(o.executed for o in obs)
    assert all(o.refusal_reason is None for o in obs)


# ── Fill size: `executed` alone hides a partial ──────────────────────────────

def test_the_filled_notional_travels_with_the_observation():
    """The simulator clamps buys to buying power, so a $5,000 request that
    filled for $50 is routine — and indistinguishable from a full fill if all
    we record is a boolean. For a subsystem whose thesis is "the position floor
    refused everything", the size is part of the measurement."""
    doc = _equity_doc()
    doc["backtest_trades"][0]["total"] = 225.0
    intc = [o for o in observations_from_backtest(doc) if o.symbol == "INTC"][0]
    assert intc.executed is True
    assert intc.filled_notional == 225.0


def test_an_unfilled_decision_has_no_notional():
    xom = [o for o in observations_from_backtest(_equity_doc())
           if o.symbol == "XOM"][0]
    assert xom.filled_notional is None


def test_a_missing_total_does_not_break_the_join():
    doc = _equity_doc()
    doc["backtest_trades"][0].pop("total", None)
    intc = [o for o in observations_from_backtest(doc) if o.symbol == "INTC"][0]
    assert intc.executed is True and intc.filled_notional == 0.0
