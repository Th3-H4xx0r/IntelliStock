"""Task 10: separate probability/return calibration, cross-fit + shrinkage."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.calibration import CalibrationError, ForecastCalibrator


def _rows(n=120):
    rows = []
    for i in range(n):
        score = (i % 41) / 10.0 - 2.0  # -2.0 .. +2.0
        excess = 0.01 * score + (0.004 if i % 7 == 0 else -0.002)
        rows.append({"raw_score": score, "excess_return": excess,
                     "as_of_date": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"})
    return rows


def test_fit_requires_100_resolved_observations_with_both_classes():
    with pytest.raises(CalibrationError):
        ForecastCalibrator().fit(_rows(80), "DIRECT", 3)
    one_sided = [{"raw_score": 1.0, "excess_return": 0.01,
                  "as_of_date": f"2026-01-{(i % 28) + 1:02d}"} for i in range(120)]
    with pytest.raises(CalibrationError):
        ForecastCalibrator().fit(one_sided, "DIRECT", 3)


def test_predicted_probability_is_monotonic_and_bounded():
    model = ForecastCalibrator().fit(_rows(), "DIRECT", 3)
    prior = 0.0
    for score in [-2.0, -1.0, 0.0, 1.0, 2.0]:
        _, prob, _ = model.predict(score)
        assert 0.0 <= prob <= 1.0
        assert prob >= prior - 1e-9  # isotonic
        prior = prob


def test_return_and_probability_are_separate_artifacts_with_shrinkage():
    model = ForecastCalibrator().fit(_rows(), "DIRECT", 3)
    expected, prob, uncertainty = model.predict(2.0)
    raw_mean_at_top = 0.01 * 2.0
    assert 0 < expected < raw_mean_at_top  # shrunk toward zero, not raw-mapped
    assert uncertainty > 0
    assert model.probability_breakpoints != model.return_breakpoints


def test_serialization_is_json_not_pickle():
    model = ForecastCalibrator().fit(_rows(), "DIRECT", 3)
    payload = model.to_json()
    parsed = json.loads(payload)  # valid JSON
    assert parsed["sample_count"] == 120
    assert parsed["evidence_class"] == "DIRECT"
    assert parsed["horizon_trading_days"] == 3
    assert "training_start" in parsed and "training_end" in parsed
    from benchmark_alpha.calibration import CalibratedModel
    restored = CalibratedModel.from_json(payload)
    assert restored.predict(1.0) == model.predict(1.0)


def test_pav_pooled_blocks_apply_from_their_left_edge():
    """Audit finding 1: PAV kept the pooled block's RIGHT-edge x, so points
    inside a merged block predicted the PREVIOUS block's value."""
    from benchmark_alpha.calibration import _pav, _step_interpolate
    curve = _pav([(0, 0.0), (1, 1.0), (2, 0.9), (3, 0.8), (4, 0.7)])
    for x in (1.0, 2.0, 3.0, 4.0):
        assert _step_interpolate(curve, x) == pytest.approx(0.85)
    assert _step_interpolate(curve, 0.0) == pytest.approx(0.0)


def test_rows_without_a_provable_date_are_excluded():
    """Audit finding 7: None-date rows fell into every training fold and no
    test fold. They are now excluded as unprovable."""
    rows = [{"raw_score": (i % 41) / 10.0 - 2.0,
             "excess_return": 0.01 * ((i % 41) / 10.0 - 2.0) + (0.004 if i % 7 == 0 else -0.002),
             "as_of_date": None} for i in range(120)]
    with pytest.raises(CalibrationError):
        ForecastCalibrator().fit(rows, "DIRECT", 3)
