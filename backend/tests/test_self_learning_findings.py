import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.findings import (
    finding_from_funnel, finding_from_variance, findings_for_run,
)
from self_learning.types import Observation
from self_learning.variance import assess_variance


def _saturated():
    return assess_variance([1.0] * 717 + [0.4] * 6, field_name="normalized_score")


def _healthy():
    return assess_variance([i / 100.0 for i in range(100)], field_name="normalized_score")


def test_a_saturated_field_raises_a_high_severity_finding():
    f = finding_from_variance(_saturated(), target="equity/nexus",
                              run_id="559934", detected_at="2026-08-15T00:00:00Z")
    assert f is not None
    assert f.kind == "constant_signal" and f.severity == "high"
    assert f.evidence["top_share"] > 0.99


def test_a_healthy_field_raises_nothing():
    assert finding_from_variance(_healthy(), target="equity/nexus", run_id="1",
                                 detected_at="t") is None


def test_the_same_defect_on_the_same_target_is_the_same_finding():
    a = finding_from_variance(_saturated(), target="equity/nexus", run_id="1", detected_at="t1")
    b = finding_from_variance(_saturated(), target="equity/nexus", run_id="2", detected_at="t2")
    assert a.id == b.id, "re-detecting must update one thread, not spawn a new one per run"


def test_the_same_defect_on_a_different_target_is_a_different_finding():
    a = finding_from_variance(_saturated(), target="equity/nexus", run_id="1", detected_at="t")
    b = finding_from_variance(_saturated(), target="crypto/meanrev", run_id="1", detected_at="t")
    assert a.id != b.id


def test_a_run_that_refused_most_of_its_buys_raises_a_conversion_finding():
    f = finding_from_funnel({"decided": 200, "executed": 6, "refused": 140,
                             "buy_decided": 146, "buy_executed": 6},
                            target="equity/nexus", run_id="559934", detected_at="t")
    assert f is not None and f.kind == "buy_conversion"
    assert f.evidence["buy_executed"] == 6


def test_a_run_that_converted_its_buys_raises_nothing():
    assert finding_from_funnel({"decided": 100, "executed": 90, "refused": 2,
                                "buy_decided": 40, "buy_executed": 38},
                               target="equity/nexus", run_id="1",
                               detected_at="t") is None


def test_a_tiny_refusal_count_is_not_a_finding():
    assert finding_from_funnel({"decided": 10, "executed": 1, "refused": 3,
                                "buy_decided": 4, "buy_executed": 1},
                               target="equity/nexus", run_id="1",
                               detected_at="t") is None


def test_findings_for_run_returns_both_when_both_apply():
    obs = [Observation(run_id="1", origin="backtest", venue="equity",
                       strategy_id="s", as_of=f"2026-04-{i:02d}", symbol="X",
                       action="buy", decision=1, normalized_score=1.0,
                       executed=False, refusal_reason="unknown", votes=(),
                       config_hash=None) for i in range(1, 32)]
    out = findings_for_run(obs, {"decided": 31, "executed": 0, "refused": 31,
                                 "buy_decided": 31, "buy_executed": 0},
                           target="equity/nexus", run_id="1", detected_at="t")
    assert {f.kind for f in out} == {"constant_signal", "buy_conversion"}
