"""Unit tests for the pure bot trade-decision log helpers (no DB)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_decision_log as bdl


def _ss():
    # Two strategies vote BUY (decision 1), one votes SELL (-1).
    return [
        {"strategy": "momentum", "weight": 0.6, "decision": 1,
         "reason": "20/50 EMA cross up", "action_intent": "momentum_amplifier_buy"},
        {"strategy": "mean_revert", "weight": 0.2, "decision": 1, "reason": "below band"},
        {"strategy": "trend_guard", "weight": 0.9, "decision": -1, "reason": "downtrend"},
    ]


def test_primary_picks_highest_weight_aligned_strategy():
    # No override → the highest-weight strategy that voted the *decision* wins,
    # NOT the globally-highest-weight one (trend_guard voted the other way).
    strat, intent, reason, override = bdl.primary_decision(_ss(), [], 1, 1)
    assert strat == "momentum"
    assert intent == "momentum_amplifier_buy"
    assert reason == "20/50 EMA cross up"
    assert override is False


def test_post_decision_override_wins_and_flags_override():
    post = [{"strategy": "ai-trading-decision", "reason": "Earnings risk; flip to sell"}]
    # pre_override_decision=1 but final decision=-1 → override applied.
    strat, intent, reason, override = bdl.primary_decision(_ss(), post, 1, -1)
    assert strat == "ai-trading-decision"
    assert reason == "Earnings risk; flip to sell"
    assert override is True


def test_contributors_are_aligned_sorted_and_trimmed():
    contribs = bdl.decision_contributors(_ss(), 1, limit=4)
    assert [c["strategy"] for c in contribs] == ["momentum", "mean_revert"]
    assert contribs[0]["weight"] == 0.6  # highest-weight aligned first
    # The sell-voting strategy is excluded from a BUY's contributors.
    assert all(c["decision"] == 1 for c in contribs)


def test_contributors_reason_capped_at_300():
    big = [{"strategy": "x", "weight": 1.0, "decision": 1, "reason": "z" * 500}]
    assert len(bdl.decision_contributors(big, 1)[0]["reason"]) == 300


def test_build_doc_shape_and_side_mapping():
    doc = bdl.build_decision_doc(
        instance_id="inst-1", brokerage_id="brk-9", symbol="aapl",
        decision=1, price=187.25, ts="2026-06-18T15:30:00Z",
        strategy_summary=_ss(), post_decision_trace=[], pre_override_decision=1,
        normalized=0.733333, doc_id="fixed-id", now_iso="2026-06-18T15:30:01Z",
    )
    assert doc["id"] == "fixed-id"
    assert doc["instance_id"] == "inst-1"
    assert doc["brokerage_id"] == "brk-9"
    assert doc["symbol"] == "AAPL"          # upper-cased
    assert doc["side"] == "buy"             # decision +1
    assert doc["price"] == 187.25
    assert doc["ts"] == "2026-06-18T15:30:00Z"
    assert doc["strategy"] == "momentum"
    assert doc["score"] == 0.7333           # rounded to 4dp
    assert doc["override_applied"] is False
    assert len(doc["contributors"]) == 2


def test_build_doc_sell_side_and_none_price():
    doc = bdl.build_decision_doc(
        "i", "b", "MSFT", -1, None, None, _ss(), [], -1, None,
        doc_id="d", now_iso="2026-06-18T00:00:00Z",
    )
    assert doc["side"] == "sell"
    assert doc["price"] is None
    assert doc["score"] is None             # normalized=None → None, not crash
    # ts falls back to now_iso when no trade timestamp given.
    assert doc["ts"] == "2026-06-18T00:00:00Z"
