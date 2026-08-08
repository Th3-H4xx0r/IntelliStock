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


# ── bt 820236: the core saw-toothed on the lane the turnover budget ignores ──

from backtest_risk_metrics import (  # noqa: E402
    sleeve_churn,
    sleeve_symbols_from_schema,
)


def _t(action, ticker, total):
    return {"action": action, "ticker": ticker, "total": total}


def test_bt820236_shape_is_reported_as_churn():
    """The real fill sequence: an opening deploy, then alternating legs."""
    trades = [_t("buy", "SPY", 2398.0)] + [
        _t(side, "SPY", amt) for side, amt in
        [("sell", 779.0), ("buy", 654.0), ("sell", 633.0), ("buy", 490.0),
         ("sell", 449.0), ("buy", 443.0), ("sell", 390.0)]
    ]
    out = sleeve_churn(trades, {"SPY"})
    assert out["fill_count"] == 8
    assert out["side_flips"] == 7, "alternating legs, not a directional move"
    assert out["post_initial_gross_notional"] == pytest.approx(3838.0)
    # a lot of trading for very little net position change
    assert out["churn_ratio"] > 2.0


def test_a_directional_move_is_not_flagged_as_churn():
    trades = [_t("buy", "SPY", 1000.0), _t("buy", "SPY", 1000.0)]
    out = sleeve_churn(trades, {"SPY"})
    assert out["side_flips"] == 0
    assert out["churn_ratio"] == pytest.approx(1.0)


def test_a_perfect_round_trip_is_none_not_zero():
    """Zero net change is the pathological case; it must not read as clean."""
    trades = [_t("buy", "SPY", 500.0), _t("sell", "SPY", 500.0)]
    assert sleeve_churn(trades, {"SPY"})["churn_ratio"] is None


def test_only_sleeve_legs_are_counted():
    trades = [_t("buy", "SPY", 500.0), _t("buy", "SNDK", 900.0),
              _t("sell", "SQQQ", 300.0)]
    out = sleeve_churn(trades, {"SPY", "SQQQ"})
    assert out["fill_count"] == 2
    assert out["gross_notional"] == pytest.approx(800.0)


def test_no_sleeve_activity_is_zeroed_not_none():
    out = sleeve_churn([_t("buy", "SNDK", 900.0)], {"SPY"})
    assert out["fill_count"] == 0 and out["gross_notional"] == 0.0


def test_malformed_trades_are_skipped():
    trades = ["junk", {"action": "buy"}, {"action": "hold", "ticker": "SPY", "total": 5.0},
              {"action": "buy", "ticker": "SPY", "total": "x"},
              _t("buy", "SPY", 100.0)]
    assert sleeve_churn(trades, {"SPY"})["fill_count"] == 1


def test_sleeve_symbols_read_off_the_stored_schema():
    schema = {"strategies": [{"config": {
        "residual_sleeve_enabled": True,
        "residual_sleeve_symbol": " spy ",
        "residual_sleeve_bear_symbol": "SQQQ"}}]}
    assert sleeve_symbols_from_schema(schema) == {"SPY", "SQQQ"}


def test_disabled_sleeve_contributes_no_symbols():
    schema = {"strategies": [{"config": {"residual_sleeve_enabled": False,
                                         "residual_sleeve_symbol": "SPY"}}]}
    assert sleeve_symbols_from_schema(schema) == set()
    assert sleeve_symbols_from_schema(None) == set()
