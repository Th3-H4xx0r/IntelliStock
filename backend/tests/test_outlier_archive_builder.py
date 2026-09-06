"""Feature preparation invariants; no portfolio or return simulation."""
import sys
from pathlib import Path
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_versioned_outlier_features import symbol_rows, rank_session, publish_rows


def bars(prices, volume=1000):
    return [{"t": (date(2020, 1, 1) + timedelta(days=i)).isoformat(),
             "c": p, "v": volume} for i, p in enumerate(prices)]


def test_early_liquid_later_dead_security_is_retained():
    raw = bars([10] * 150 + [.01] * 100)
    rows = symbol_rows("DEAD", raw, raw, "v1", adv_min=9000)
    assert len(rows) == 250
    assert rows[130]["rank_eligible"] is True
    assert rows[-1]["rank_eligible"] is False


def test_future_price_does_not_change_earlier_features_or_rank_eligibility():
    first = bars([10 + i / 100 for i in range(150)])
    future = bars([10 + i / 100 for i in range(150)] + [100] * 30)
    a = symbol_rows("AAA", first, first, "v1", adv_min=9000)
    b = symbol_rows("AAA", future, future, "v1", adv_min=9000)
    assert a == b[:len(a)]


def test_raw_prices_and_volume_control_eligibility_after_future_split():
    raw = bars([10] * 150)
    adjusted = bars([1] * 150, volume=10000)
    row = symbol_rows("AAA", adjusted, raw, "v1", adv_min=9000)[-1]
    assert row["close"] == 1
    assert row["nominal_close"] == 10
    assert row["adv20"] == 10000
    assert row["rank_eligible"] is True


def test_missing_raw_bar_refuses_misaligned_history():
    raw = bars([10] * 150)
    with pytest.raises(ValueError, match="date mismatch"):
        symbol_rows("AAA", raw, raw[:-1], "v1", adv_min=9000)


def test_currently_ineligible_future_winner_does_not_change_past_ranks():
    rows = [{"symbol": "AAA", "ret126": .2, "rank_eligible": True},
            {"symbol": "BBB", "ret126": .4, "rank_eligible": True},
            {"symbol": "LATER", "ret126": 10, "rank_eligible": False}]
    rank_session(rows)
    assert [r["rs_rank"] for r in rows] == [0, 1, None]


def test_equal_returns_receive_equal_ranks():
    rows = [{"symbol": s, "ret126": ret, "rank_eligible": True}
            for s, ret in [("AAA", .1), ("BBB", .1), ("CCC", .3)]]
    rank_session(rows)
    assert rows[0]["rs_rank"] == rows[1]["rs_rank"] == .25


def test_completed_dataset_is_immutable(store):
    store.insert("PointInTimeDatasetSnapshots", {"id": "outlier:v1", "complete": True})
    with pytest.raises(ValueError, match="already exists"):
        publish_rows(store, "v1", [], {"build_id": "new"})


def test_manifest_completes_only_after_successful_rows(store):
    def failing():
        yield [{"id": "v1|2026-01-02|AAA", "date": "2026-01-02", "symbol": "AAA"}]
        raise RuntimeError("source failed")
    with pytest.raises(RuntimeError, match="source failed"):
        publish_rows(store, "v1", failing(), {"build_id": "abc"})
    assert store.get("PointInTimeDatasetSnapshots", "outlier:v1")["complete"] is False
