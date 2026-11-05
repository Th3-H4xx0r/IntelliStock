"""GET /backtests/{id}/summary carries the TR scorecard (plan Task 11).

The web and iOS backtest pages read the run's figures from the summary
endpoint, so ``tr_scorecard`` must ride along verbatim when the result has it
and stay absent (None) when it does not.
"""
from __future__ import annotations

import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import backtest_result_store as brs  # noqa: E402
import interactive_utils as iu  # noqa: E402
from db import store  # noqa: E402


def _doc(**extra):
    doc = {"id": 900001, "status": "finished", "pnl": 10.0, "pnl_percent": 0.5,
           "start_date": "2024-01-02", "end_date": "2024-06-28",
           "portfolio_value_history": [], "backtest_trades": []}
    doc.update(extra)
    return doc


@pytest.fixture
def summarize(monkeypatch):
    def run(doc):
        monkeypatch.setattr(store, "table_list", lambda: ["BacktestResults"])
        monkeypatch.setattr(brs, "assemble", lambda bid: dict(doc))
        return iu.action_summarize_backtest(None, doc["id"])
    return run


def test_summary_passes_the_scorecard_through(summarize):
    card = {"overall": {"cagr": 12.3, "pass_plus5": "untested"},
            "honest": {"cagr": None}, "optimistic": {"cagr": 1.0}, "meta": {"notes": []}}
    out = summarize(_doc(tr_scorecard=card))
    assert out["tr_scorecard"] == card


def test_summary_passes_a_scorecard_error_through(summarize):
    out = summarize(_doc(tr_scorecard={"error": "ValueError: no bars"}))
    assert out["tr_scorecard"] == {"error": "ValueError: no bars"}


def test_summary_without_a_scorecard_reads_none(summarize):
    out = summarize(_doc())
    assert out["tr_scorecard"] is None
