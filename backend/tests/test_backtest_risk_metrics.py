"""The equity curve the promotion gate needs, and the number it was given.

Six of `benchmark_alpha.promotion`'s criteria are evaluated against an equity
curve. The backtest builds one (`portfolio_value_history`) and discarded it,
keeping only `portfolio_value_high` / `portfolio_value_low`.

Those are run EXTREMES. bt 820236 reported high $6,974.29 and low $5,869.45;
(high - low) / high = 15.84% reads like a breach of the gate's 15% max-drawdown
limit, but the low sits BELOW the $6,000 start, so it precedes the peak and is
not a drawdown at all. `test_the_extremes_lie_about_drawdown` pins that exact
case.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest_risk_metrics import (  # noqa: E402
    downsample,
    max_drawdown,
    period_returns,
    risk_metrics,
    volatility,
)


def _h(*values):
    return [{"timestamp": f"t{i}", "value": v, "cash": 0.0}
            for i, v in enumerate(values)]


def test_the_extremes_lie_about_drawdown():
    """bt 820236's shape: dip below the start FIRST, then rally to the peak."""
    hist = _h(6000.0, 5869.45, 6400.0, 6974.29, 6739.61)

    naive = (6974.29 - 5869.45) / 6974.29          # what high/low implied
    assert naive == pytest.approx(0.1584, abs=0.0005)

    true = max_drawdown(hist)["max_drawdown_pct"]  # peak must precede trough
    assert true == pytest.approx((6974.29 - 6739.61) / 6974.29, abs=1e-9)
    assert true == pytest.approx(0.0336, abs=0.0005)
    assert true < 0.15 < naive, "the gate would have failed on a number nobody lost"


def test_drawdown_uses_the_preceding_peak_only():
    hist = _h(100.0, 80.0, 200.0, 190.0)
    dd = max_drawdown(hist)
    assert dd["max_drawdown_pct"] == pytest.approx(0.20)
    assert dd["peak_value"] == pytest.approx(100.0)
    assert dd["trough_value"] == pytest.approx(80.0)


def test_deepest_of_several_declines_wins():
    hist = _h(100.0, 95.0, 120.0, 60.0, 130.0)
    assert max_drawdown(hist)["max_drawdown_pct"] == pytest.approx(0.5)


def test_a_monotonic_curve_has_zero_drawdown():
    assert max_drawdown(_h(100.0, 110.0, 120.0))["max_drawdown_pct"] == pytest.approx(0.0)


def test_unmeasured_is_none_not_zero():
    """'no drawdown' and 'not measured' must not read the same to a gate."""
    assert max_drawdown([])["max_drawdown_pct"] is None
    assert max_drawdown(_h(100.0))["max_drawdown_pct"] is None
    assert risk_metrics([])["max_drawdown_pct"] is None


def test_malformed_points_are_skipped_not_fatal():
    hist = [{"value": 100.0}, {"value": None}, "junk", {"value": "x"}, {"value": 50.0}]
    assert max_drawdown(hist)["max_drawdown_pct"] == pytest.approx(0.5)


def test_returns_and_volatility():
    assert period_returns(_h(100.0, 110.0, 99.0)) == pytest.approx([0.1, -0.1])
    assert volatility(_h(100.0, 110.0, 99.0)) == pytest.approx(0.1414, abs=1e-3)
    assert volatility(_h(100.0)) is None


def test_downsample_keeps_shape_and_both_endpoints():
    hist = _h(*[float(i) for i in range(1, 2001)])
    out = downsample(hist, max_points=100)
    assert len(out) == 100
    assert out[0] is hist[0]
    assert out[-1] is hist[-1]


def test_downsample_is_a_noop_when_short_enough():
    hist = _h(1.0, 2.0, 3.0)
    assert downsample(hist, max_points=512) == hist


def test_risk_metrics_block_shape():
    m = risk_metrics(_h(6000.0, 5869.45, 6400.0, 6974.29, 6739.61))
    assert set(m) == {"max_drawdown_pct", "max_drawdown_peak_value",
                      "max_drawdown_trough_value", "return_volatility",
                      "observation_count", "equity_curve"}
    assert m["observation_count"] == 5
    assert m["max_drawdown_peak_value"] == pytest.approx(6974.29)
    assert len(m["equity_curve"]) == 5
