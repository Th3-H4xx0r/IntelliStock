"""Gate refusals — the population the subsystem exists to study.

`backtest_decisions` records only what SURVIVED the gates: `broker.py` writes it
under `not _trade_skipped_no_price`, and the comment at the min-position floor
says it plainly — `# reuse flag to prevent recording`. So the names refused at a
gate were exactly the ones missing, which is why "0 of 134 grants cleared the
min-position floor" took a human reading logs to find.

`backtest_refusals` closes that. These tests cover the consuming half; the
producing half is an optimistic register in the order loop, covered by
`test_broker_gate_refusal_register.py`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.observers import (
    all_observations, funnel_summary, observations_from_gate_refusals,
)
from self_learning.pipeline import process_backtest_document


def _doc():
    """A run that decided four buys, executed one, and had three refused at
    gates before they ever reached the execution path."""
    return {
        "id": 559934,
        "backtest_decisions": [
            {"timestamp": "2026-04-01T13:30:00", "symbol": "INTC", "action": "buy",
             "decision": 1, "normalized_score": 1.0,
             "primary_strategy": "graph_nexus_analysis", "strategies": []},
        ],
        "backtest_trades": [
            {"timestamp": "2026-04-01T13:45:00+00:00", "action": "buy",
             "ticker": "INTC", "shares": 10.0, "price": 22.5},
        ],
        "backtest_refusals": [
            {"timestamp": "2026-04-01T13:30:00", "symbol": "DELL", "action": "buy",
             "decision": 1, "normalized_score": 1.0,
             "primary_strategy": "graph_nexus_analysis",
             "reason": "min_position_floor"},
            {"timestamp": "2026-04-01T13:30:00", "symbol": "SNDK", "action": "buy",
             "decision": 1, "normalized_score": 1.0,
             "primary_strategy": "graph_nexus_analysis",
             "reason": "min_position_floor"},
            {"timestamp": "2026-04-01T13:30:00", "symbol": "MU", "action": "buy",
             "decision": 1, "normalized_score": 1.0,
             "primary_strategy": "graph_nexus_analysis",
             "reason": "max_positions"},
        ],
    }


def test_a_gate_refusal_becomes_an_observation():
    obs = observations_from_gate_refusals(_doc())
    assert {o.symbol for o in obs} == {"DELL", "SNDK", "MU"}


def test_a_gate_refusal_carries_the_gate_that_refused_it():
    by_symbol = {o.symbol: o for o in observations_from_gate_refusals(_doc())}
    assert by_symbol["DELL"].refusal_reason == "min_position_floor"
    assert by_symbol["MU"].refusal_reason == "max_positions"


def test_a_gate_refusal_never_counts_as_executed():
    assert all(not o.executed for o in observations_from_gate_refusals(_doc()))


def test_the_two_sources_are_disjoint_and_both_present():
    obs = all_observations(_doc())
    assert len(obs) == 4
    assert sum(1 for o in obs if o.executed) == 1
    assert sum(1 for o in obs if o.refusal_reason not in (None, "unfilled")) == 3


def test_the_summary_breaks_the_refusals_down_by_gate():
    summary = funnel_summary(_doc())
    assert summary["gate_refused"] == 3
    assert summary["gate_reasons"] == {"min_position_floor": 2, "max_positions": 1}
    assert summary["gate_refusals_available"] is True


def test_buy_conversion_now_counts_the_gated_names_too():
    """Before gate capture this run looked like 1 of 1 buys converting — a
    perfect 100%. It is actually 1 of 4."""
    summary = funnel_summary(_doc())
    assert summary["buy_decided"] == 4
    assert summary["buy_executed"] == 1


def test_a_run_without_the_field_reports_unavailable_not_zero():
    doc = _doc()
    doc.pop("backtest_refusals")
    summary = funnel_summary(doc)
    assert summary["gate_refused"] == 0
    assert summary["gate_refusals_available"] is False


def test_gate_refusals_reach_the_pipeline_and_its_findings():
    """A run whose buys are overwhelmingly gate-refused must surface as a
    conversion finding, not as a healthy run with one clean fill."""
    doc = _doc()
    doc["backtest_refusals"] = [
        {"timestamp": f"2026-04-{(i % 28) + 1:02d}T13:30:00", "symbol": f"G{i}",
         "action": "buy", "decision": 1, "normalized_score": 0.5,
         "primary_strategy": "graph_nexus_analysis",
         "reason": "min_position_floor"} for i in range(40)
    ]
    out = process_backtest_document(doc, detected_at="t")
    assert out["summary"]["gate_refused"] == 40
    assert "buy_conversion" in {f.kind for f in out["findings"]}


def test_a_malformed_refusal_row_is_skipped_not_fatal():
    doc = _doc()
    doc["backtest_refusals"] = [None, "nonsense", {"decision": 0}]
    assert observations_from_gate_refusals(doc) == []
