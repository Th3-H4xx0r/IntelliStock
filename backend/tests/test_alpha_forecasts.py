"""Task 10: Graph Nexus evidence -> typed forecasts. Reason text is
explanatory only; every legacy override lane is non-executable; the BDC/
COHR/BV/MRNA contradictions fail closed with auditable rejections."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.calibration import ForecastCalibrator
from benchmark_alpha.forecast_adapters import graph_forecasts
from benchmark_alpha.types import EvidenceClass, RunOrigin

AS_OF = datetime(2026, 7, 11, 14, 30, tzinfo=timezone.utc)
CTX = dict(run_id="run-1", instance_id="alpaca-main", origin=RunOrigin.LIVE,
           as_of=AS_OF, data_snapshot_id="snap-1", model_version="gn-1",
           feature_version="f1")


def _calibrators():
    rows = []
    for i in range(120):
        score = (i % 41) / 10.0 - 2.0
        rows.append({"raw_score": score,
                     "excess_return": 0.01 * score + (0.004 if i % 7 == 0 else -0.002),
                     "as_of_date": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"})
    model = ForecastCalibrator().fit(rows, "DIRECT", 3)
    return {("DIRECT", h): model for h in (1, 3, 5)}


def _payload(**kw):
    base = dict(score=2, action_intent="initial_buy", reason="strong signal",
                evidence="direct", has_graph_signal=True, quality_ok=True,
                trend_evidence="positive", price_available=True,
                ml_enabled=True, ml_output_present=False,
                source_lane="fresh_direct")
    base.update(kw)
    return base


def test_eligible_fresh_direct_emits_three_calibrated_horizons():
    out = graph_forecasts({"AAPL": _payload()}, {}, _calibrators(), CTX)
    aapl = [f for f in out if f.symbol == "AAPL"]
    assert sorted(f.horizon_trading_days for f in aapl) == [1, 3, 5]
    assert all(f.eligibility for f in aapl)
    assert all(f.evidence_class is EvidenceClass.DIRECT for f in aapl)
    assert all(0.0 <= f.probability_outperform <= 1.0 for f in aapl)


def test_missing_calibration_is_uncalibrated_never_raw_mapped():
    out = graph_forecasts({"AAPL": _payload()}, {}, {}, CTX)
    assert all(not f.eligibility for f in out)
    assert all("uncalibrated" in f.gate_reasons for f in out)
    assert all(f.expected_excess_return == 0.0 for f in out)


def test_every_legacy_override_lane_is_non_executable():
    lanes = ("backfill_rotation", "backfill_queue", "scheduled_vote",
             "breakout_override", "reserved_slot", "high_conviction_bypass",
             "queued_grace")
    for lane in lanes:
        out = graph_forecasts(
            {"XYZ": _payload(source_lane=lane)}, {}, _calibrators(), CTX)
        assert all(not f.eligibility for f in out), lane
        assert any(lane in reason for f in out for reason in f.gate_reasons)


def test_propagation_evidence_is_research_only():
    out = graph_forecasts({"PRP": _payload(evidence="propagation")}, {},
                          _calibrators(), CTX)
    assert all(f.evidence_class is EvidenceClass.PROPAGATION for f in out)
    assert all(not f.eligibility for f in out)


def test_contradictory_evidence_fixtures_fail_closed():
    """Sanitized BDC/COHR/BV/MRNA regressions from the July forensics."""
    scores = {
        "BDC": _payload(source_lane="backfill_rotation",
                        has_graph_signal=False, reason="No graph signal"),
        "COHR": _payload(source_lane="reserved_slot",
                         has_graph_signal=False, reason="No graph signal"),
        "BV": _payload(source_lane="backfill_rotation", quality_ok=False),
        "MRNA": _payload(trend_evidence="negative"),
    }
    out = graph_forecasts(scores, {}, _calibrators(), CTX)
    assert out and all(not f.eligibility for f in out)
    by_symbol = {}
    for f in out:
        by_symbol.setdefault(f.symbol, set()).update(f.gate_reasons)
    assert any("no_graph_signal" in r for r in by_symbol["BDC"])
    assert any("failed_quality" in r for r in by_symbol["BV"])
    assert any("negative_trend" in r for r in by_symbol["MRNA"])


def test_unknown_reason_missing_price_and_disabled_ml_are_ineligible():
    scores = {
        "UNK": _payload(action_intent="unknown"),
        "NOPX": _payload(price_available=False),
        "MLX": _payload(ml_enabled=False, ml_output_present=True),
    }
    out = graph_forecasts(scores, {}, _calibrators(), CTX)
    by_symbol = {}
    for f in out:
        by_symbol.setdefault(f.symbol, set()).update(f.gate_reasons)
        assert not f.eligibility
    assert any("unknown_reason" in r for r in by_symbol["UNK"])
    assert any("missing_price" in r for r in by_symbol["NOPX"])
    assert any("disabled_model_leg" in r for r in by_symbol["MLX"])


def test_reason_text_alone_can_never_create_eligibility():
    out = graph_forecasts(
        {"HYPE": _payload(has_graph_signal=False,
                          reason="AMAZING breakout confirmed!!!")},
        {}, _calibrators(), CTX)
    assert all(not f.eligibility for f in out)
