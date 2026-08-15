import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gate_refusal_log import (
    build_refusal, finalize_refusal, primary_strategy_of,
)


def test_a_hold_is_not_a_refusal():
    """A hold is the system doing exactly what it decided. Arming a refusal for
    it would flood the register with non-events."""
    assert build_refusal(timestamp="t", symbol="X", action="hold", decision=0,
                         normalized=0.5) is None


def test_a_buy_arms_a_refusal():
    rec = build_refusal(timestamp="2026-04-01T13:30:00", symbol="dell",
                        action="Buy", decision=1, normalized=0.87654)
    assert rec["symbol"] == "DELL"
    assert rec["action"] == "buy"
    assert rec["decision"] == 1
    assert rec["normalized_score"] == 0.8765


def test_a_sell_arms_a_refusal_too():
    """A min-hold block on a sell is a refusal exactly as a floor block on a
    buy is."""
    rec = build_refusal(timestamp="t", symbol="X", action="sell", decision=-1,
                        normalized=None)
    assert rec is not None and rec["decision"] == -1


def test_a_datetime_timestamp_is_serialized():
    rec = build_refusal(timestamp=datetime.datetime(2026, 4, 1, 13, 30),
                        symbol="X", action="buy", decision=1, normalized=1.0)
    assert rec["timestamp"] == "2026-04-01T13:30:00"


def test_a_non_numeric_score_does_not_raise():
    rec = build_refusal(timestamp="t", symbol="X", action="buy", decision=1,
                        normalized="not-a-number")
    assert rec["normalized_score"] is None


def test_attribution_uses_the_heaviest_contributor():
    summary = [{"strategy": "rsi", "weight": 0.2},
               {"strategy": "graph_nexus_analysis", "weight": 0.8}]
    assert primary_strategy_of(summary) == "graph_nexus_analysis"


def test_attribution_of_nothing_is_none_not_a_crash():
    assert primary_strategy_of(None) is None
    assert primary_strategy_of([]) is None
    assert primary_strategy_of(["nonsense"]) is None


def test_the_reason_is_stamped_at_finalize():
    pending = build_refusal(timestamp="t", symbol="X", action="buy",
                            decision=1, normalized=1.0)
    assert finalize_refusal(pending, "min_position_floor")["reason"] == \
        "min_position_floor"


def test_an_unlabelled_gate_still_records_the_refusal():
    """Losing the fact because the reason is unknown is worse than a generic
    reason code."""
    pending = build_refusal(timestamp="t", symbol="X", action="buy",
                            decision=1, normalized=1.0)
    assert finalize_refusal(pending, None)["reason"] == "gate"


def test_finalize_does_not_mutate_the_armed_record():
    pending = build_refusal(timestamp="t", symbol="X", action="buy",
                            decision=1, normalized=1.0)
    finalize_refusal(pending, "max_positions")
    assert "reason" not in pending


def test_finalizing_nothing_is_none():
    assert finalize_refusal(None, "x") is None
