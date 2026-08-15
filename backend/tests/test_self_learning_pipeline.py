import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.pipeline import process_backtest_document


def _saturated_doc():
    """40 buy decisions, all scoring exactly 1.0, only one of which filled —
    the shape this project's real runs actually had."""
    decisions = [
        {"timestamp": f"2026-04-{(i % 28) + 1:02d}T13:30:00", "symbol": f"S{i}",
         "action": "buy", "decision": 1, "normalized_score": 1.0,
         "primary_strategy": "graph_nexus_analysis", "strategies": []}
        for i in range(40)
    ]
    return {"id": 559934, "backtest_decisions": decisions,
            "backtest_trades": [{"timestamp": "2026-04-01T13:30:00",
                                 "action": "buy", "ticker": "S0",
                                 "shares": 1, "price": 1.0}]}


def test_the_pipeline_produces_observations_findings_and_a_summary():
    out = process_backtest_document(_saturated_doc(), detected_at="t")
    assert len(out["observations"]) == 40
    assert out["summary"]["buy_decided"] == 40
    assert out["summary"]["buy_executed"] == 1


def test_it_raises_both_guards_on_a_run_shaped_like_the_real_ones():
    out = process_backtest_document(_saturated_doc(), detected_at="t")
    assert {f.kind for f in out["findings"]} == {"constant_signal", "buy_conversion"}


def test_the_target_is_derived_from_the_data_not_hardcoded():
    out = process_backtest_document(_saturated_doc(), detected_at="t")
    assert out["target"] == "equity/graph_nexus_analysis"


def test_a_different_strategy_yields_a_different_target():
    doc = _saturated_doc()
    for d in doc["backtest_decisions"]:
        d["primary_strategy"] = "rsi"
    assert process_backtest_document(doc, detected_at="t")["target"] == "equity/rsi"


def test_an_empty_document_is_handled_without_raising():
    out = process_backtest_document({"id": 1}, detected_at="t")
    assert out["observations"] == [] and out["findings"] == []


def test_thresholds_are_injected_not_hardcoded():
    # With min_n above the sample size the variance guard must stay silent.
    out = process_backtest_document(_saturated_doc(), detected_at="t",
                                    variance_min_n=1000)
    assert "constant_signal" not in {f.kind for f in out["findings"]}
