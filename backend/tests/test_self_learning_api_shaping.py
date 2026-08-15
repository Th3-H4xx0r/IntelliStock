import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.api_shape import overview


def test_overview_counts_open_findings_by_severity():
    out = overview(
        findings=[{"severity": "high", "status": "open"},
                  {"severity": "high", "status": "open"},
                  {"severity": "medium", "status": "open"},
                  {"severity": "high", "status": "closed"}],
        funnels=[], config={"mode": "observe", "enabled": True})
    assert out["open_findings"] == 3
    assert out["by_severity"] == {"high": 2, "medium": 1}


def test_overview_reports_observed_runs_and_totals():
    out = overview(findings=[],
                   funnels=[{"run_id": "1", "decided": 10, "refused": 4},
                            {"run_id": "2", "decided": 5, "refused": 1}],
                   config={"mode": "observe", "enabled": True})
    assert out["runs_observed"] == 2
    assert out["decisions_observed"] == 15
    assert out["refusals_observed"] == 5


def test_overview_surfaces_the_mode_so_the_ui_cannot_imply_autonomy():
    out = overview(findings=[], funnels=[], config={"mode": "observe", "enabled": True})
    assert out["mode"] == "observe"
    assert out["acts_autonomously"] is False


def test_overview_of_nothing_is_zeroes_not_an_error():
    out = overview(findings=[], funnels=[], config={})
    assert out["open_findings"] == 0 and out["runs_observed"] == 0
