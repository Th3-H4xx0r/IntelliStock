"""Task 11: deterministic momentum/event challenger (research control)."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.challenger import build_features, challenger_forecasts
from benchmark_alpha.types import EvidenceClass, RunOrigin

AS_OF = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
CTX = dict(run_id="run-1", instance_id="alpaca-main",
           origin=RunOrigin.BACKTEST, as_of=AS_OF, data_snapshot_id="snap-1",
           model_version="challenger-1", feature_version="cf-1")


def _bars(daily_move, days=30, start=100.0):
    out = []
    price = start
    for i in range(days):
        price *= 1.0 + daily_move
        out.append({"date": (AS_OF - timedelta(days=days - i)).date().isoformat(),
                    "close_adjusted": price})
    return out


SPY = _bars(0.001)
BARS = {"AAA": _bars(0.004), "BBB": _bars(0.001), "CCC": _bars(-0.004)}


def test_trend_ordering_and_point_in_time_slicing():
    feats = {s: build_features(BARS[s], SPY, event_score=0.0, as_of=AS_OF)
             for s in BARS}
    assert feats["AAA"].excess_5d > feats["BBB"].excess_5d > feats["CCC"].excess_5d
    # Bars after as_of must not change features.
    future = BARS["AAA"] + [{"date": (AS_OF + timedelta(days=2)).date().isoformat(),
                             "close_adjusted": 9999.0}]
    assert build_features(future, SPY, event_score=0.0, as_of=AS_OF) == feats["AAA"]


def test_forecast_scores_rank_a_over_b_over_c():
    out = challenger_forecasts(["CCC", "AAA", "BBB"], BARS, SPY,
                               {"AAA": 0.0, "BBB": 0.0, "CCC": 0.0}, {}, CTX)
    by_symbol = {f.symbol: f for f in out if f.horizon_trading_days == 3}
    assert by_symbol["AAA"].expected_excess_return == 0.0  # uncalibrated => 0
    raw = {f.symbol: f for f in out if f.horizon_trading_days == 3}
    # Raw ranking is carried in confidence-free probability ordering? No —
    # ranking is exposed via the deterministic raw score in gate metadata:
    scores = {s: f.raw_score for s, f in raw.items()}
    assert scores["AAA"] > scores["BBB"] > scores["CCC"]


def test_shuffled_input_produces_identical_order_and_ids():
    a = challenger_forecasts(["CCC", "AAA", "BBB"], BARS, SPY, {}, {}, CTX)
    b = challenger_forecasts(["BBB", "CCC", "AAA"], BARS, SPY, {}, {}, CTX)
    assert [f.record_id for f in a] == [f.record_id for f in b]
    assert all(f.evidence_class is EvidenceClass.DETERMINISTIC for f in a)


def test_zero_variance_cross_section_yields_zero_z_scores():
    same = {s: _bars(0.002) for s in ("X1", "X2")}
    out = challenger_forecasts(["X1", "X2"], same, SPY, {}, {}, CTX)
    assert all(f.raw_score == pytest.approx(0.0, abs=1e-9) for f in out)


def test_challenger_is_never_tradable_eligible_by_default():
    out = challenger_forecasts(["AAA"], BARS, SPY, {}, {}, CTX)
    assert all(not f.eligibility for f in out)
    assert all("deterministic_research_control" in f.gate_reasons for f in out)
