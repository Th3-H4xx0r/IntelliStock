import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.retention import expired_ids, rollup


def _docs():
    return [
        {"id": "old", "as_of": "2026-01-01T00:00:00", "run_id": "1",
         "strategy_id": "rsi", "decision": 1, "executed": False,
         "refusal_reason": "unknown", "normalized_score": 1.0},
        {"id": "new", "as_of": "2026-08-01T00:00:00", "run_id": "1",
         "strategy_id": "rsi", "decision": 1, "executed": True,
         "refusal_reason": None, "normalized_score": 0.4},
    ]


def test_only_rows_past_the_window_expire():
    assert expired_ids(_docs(), now_iso="2026-08-15T00:00:00", retain_days=90) == ["old"]


def test_nothing_expires_inside_the_window():
    assert expired_ids(_docs(), now_iso="2026-08-15T00:00:00", retain_days=3650) == []


def test_an_unparseable_timestamp_is_never_deleted():
    """Deleting on a parse failure would silently destroy data. Keep it."""
    docs = [{"id": "bad", "as_of": "not-a-date"}]
    assert expired_ids(docs, now_iso="2026-08-15T00:00:00", retain_days=1) == []


def test_rollup_aggregates_one_row_per_run_strategy_and_date():
    out = rollup(_docs())
    assert len(out) == 2
    assert {r["date"] for r in out} == {"2026-01-01", "2026-08-01"}


def test_rollup_preserves_the_counts_that_carry_the_learning_value():
    out = rollup(_docs() + [dict(_docs()[0], id="old2")])
    jan = [r for r in out if r["date"] == "2026-01-01"][0]
    assert jan["decided"] == 2 and jan["executed"] == 0 and jan["refused"] == 2
    assert jan["score_top_share"] == 1.0
