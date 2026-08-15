import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.api_shape import overview


def test_overview_passes_through_database_computed_counters():
    """The counters are aggregated BY RethinkDB. Summing a 500-row slice in
    Python and calling it a total pinned runs_observed at 500 forever."""
    out = overview(
        counts={"open_findings": 3, "by_severity": {"high": 2, "medium": 1},
                "runs_observed": 812, "decisions_observed": 41_000,
                "refusals_observed": 9_100},
        config={"mode": "observe", "enabled": True})
    assert out["open_findings"] == 3
    assert out["by_severity"] == {"high": 2, "medium": 1}
    assert out["runs_observed"] == 812
    assert out["decisions_observed"] == 41_000
    assert out["refusals_observed"] == 9_100


def test_overview_surfaces_the_mode_so_the_ui_cannot_imply_autonomy():
    out = overview(counts={}, config={"mode": "observe", "enabled": True})
    assert out["mode"] == "observe"
    assert out["acts_autonomously"] is False


def test_a_non_observe_mode_is_reported_as_acting():
    out = overview(counts={}, config={"mode": "propose", "enabled": True})
    assert out["acts_autonomously"] is True


def test_overview_reports_whether_the_engine_is_actually_running():
    """Without this the tab cannot distinguish 'nothing found yet' from
    'the engine was never started' — and the engine ships OFF."""
    assert overview(counts={}, config={}, engine_running=False)["engine_running"] is False
    assert overview(counts={}, config={}, engine_running=True)["engine_running"] is True


def test_overview_of_nothing_is_zeroes_not_an_error():
    out = overview(counts={}, config={})
    assert out["open_findings"] == 0 and out["runs_observed"] == 0
