"""Task 9: truthful SPY-relative accounting — active metrics."""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.metrics import (
    IncompleteBenchmarkError,
    align_return_series,
    compute_active_metrics,
    deflated_sharpe_probability,
)


def test_active_metrics_use_matching_timestamps_and_known_active_return():
    idx = pd.to_datetime(["2026-07-08", "2026-07-09", "2026-07-10"], utc=True)
    aligned = pd.DataFrame({"portfolio": [100.0, 102.0, 101.0],
                            "benchmark": [100.0, 101.0, 101.0]}, index=idx)
    m = compute_active_metrics(aligned, trials=7, bootstrap_seed=179)
    assert round(m.portfolio_return, 6) == 0.01
    assert round(m.benchmark_return, 6) == 0.01
    assert round(m.active_return, 6) == 0.0
    assert m.trials == 7


def test_align_requires_complete_matching_timestamp_coverage():
    port = pd.Series([100.0, 101.0, 102.0],
                     index=pd.to_datetime(
                         ["2026-07-08", "2026-07-09", "2026-07-10"], utc=True))
    spy = pd.Series([500.0, 505.0],
                    index=pd.to_datetime(["2026-07-09", "2026-07-10"], utc=True))
    with pytest.raises(IncompleteBenchmarkError, match="missing benchmark"):
        align_return_series(port, spy)

    aligned = align_return_series(port.iloc[1:], spy)
    assert list(aligned.index) == list(port.index[1:])
    assert list(aligned.columns) == ["portfolio", "benchmark"]
    with pytest.raises(IncompleteBenchmarkError):
        align_return_series(port.iloc[:1], spy.iloc[:1])


def test_align_rejects_duplicate_normalized_dates_and_non_finite_values():
    duplicate_day = pd.Series(
        [100.0, 101.0],
        index=pd.to_datetime(
            ["2026-07-08T20:00:00Z", "2026-07-08T20:00:00Z"]
        ),
    )
    spy = pd.Series(
        [500.0],
        index=pd.to_datetime(["2026-07-08T20:00:00Z"]),
    )
    with pytest.raises(IncompleteBenchmarkError, match="duplicate"):
        align_return_series(duplicate_day, spy)
    with pytest.raises(IncompleteBenchmarkError, match="finite positive"):
        align_return_series(
            pd.Series([float("nan"), 101.0], index=pd.date_range("2026-07-08", periods=2)),
            pd.Series([500.0, 501.0], index=pd.date_range("2026-07-08", periods=2)),
        )


def test_metrics_refuse_implicit_or_nonpositive_trial_count():
    idx = pd.to_datetime(["2026-07-08", "2026-07-09"], utc=True)
    aligned = pd.DataFrame(
        {"portfolio": [100.0, 101.0], "benchmark": [100.0, 100.5]},
        index=idx,
    )
    with pytest.raises(TypeError):
        compute_active_metrics(aligned)
    with pytest.raises(ValueError, match="positive"):
        compute_active_metrics(aligned, trials=0)


def test_max_drawdown_is_positive_magnitude_and_beta_reasonable():
    idx = pd.to_datetime([f"2026-06-{d:02d}" for d in range(1, 21)], utc=True)
    bench = pd.Series([100 * (1.002 ** i) for i in range(20)], index=idx)
    port = pd.Series([100, 104, 108, 100, 92, 96, 100, 104, 108, 112,
                      110, 108, 112, 116, 114, 118, 120, 118, 122, 124],
                     index=idx, dtype=float)
    m = compute_active_metrics(align_return_series(port, bench),
                               trials=3, bootstrap_seed=179)
    assert m.max_drawdown_magnitude > 0
    assert m.max_drawdown_magnitude == pytest.approx(1 - 92.0 / 108.0)
    assert m.tracking_error > 0
    assert m.bootstrap_active_low <= m.bootstrap_active_high


def test_bootstrap_is_deterministic_for_a_seed():
    idx = pd.to_datetime([f"2026-06-{d:02d}" for d in range(1, 16)], utc=True)
    aligned = pd.DataFrame(
        {"portfolio": [100 + i + (i % 3) for i in range(15)],
         "benchmark": [100 + i for i in range(15)]}, index=idx, dtype=float)
    a = compute_active_metrics(aligned, trials=3, bootstrap_seed=179)
    b = compute_active_metrics(aligned, trials=3, bootstrap_seed=179)
    assert (a.bootstrap_active_low, a.bootstrap_active_high) == \
        (b.bootstrap_active_low, b.bootstrap_active_high)


def test_deflated_sharpe_probability_penalizes_trials():
    few = deflated_sharpe_probability(1.2, sample_count=252, skew=0.0,
                                      kurtosis=3.0, trials=1)
    many = deflated_sharpe_probability(1.2, sample_count=252, skew=0.0,
                                       kurtosis=3.0, trials=200)
    assert 0.0 <= many <= few <= 1.0
    with pytest.raises(ValueError):
        deflated_sharpe_probability(1.0, sample_count=252, skew=0.0,
                                    kurtosis=3.0, trials=0)


def test_tiny_sample_bootstrap_interval_is_not_a_zero_width_certainty():
    """Audit: n <= block made every draw identical -> width 0 false certainty."""
    idx = pd.to_datetime(["2026-07-06", "2026-07-07", "2026-07-08",
                          "2026-07-09"], utc=True)
    aligned = pd.DataFrame({"portfolio": [100.0, 103.0, 99.0, 104.0],
                            "benchmark": [100.0, 100.5, 101.0, 101.5]},
                           index=idx)
    m = compute_active_metrics(aligned, trials=3, bootstrap_seed=179)
    assert m.bootstrap_active_high > m.bootstrap_active_low
