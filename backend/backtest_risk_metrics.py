"""Risk metrics the promotion gate needs, computed from the equity curve.

Six of `benchmark_alpha.promotion`'s criteria — max_drawdown, information_ratio,
deflated_sharpe, beta, profit_factor and the unseen-quarter fraction — are
evaluated against an equity curve. The backtest builds one
(`portfolio_value_history`, entries of {timestamp, value, cash}) and then throws
it away: the result row keeps only `portfolio_value_high` and
`portfolio_value_low`.

Those two are run EXTREMES, not a drawdown. bt 820236 reported high $6,974.29
and low $5,869.45, and (high - low) / high = 15.84% reads like a breach of the
gate's 15% limit — but the low sits BELOW the $6,000 start, so it precedes the
peak and the number is not a drawdown at all. Without the ordered series there
is no way to tell those apart, so the metric cannot be evaluated honestly in
either direction.

This module is pure and side-effect free so the arithmetic can be tested
directly rather than inferred from a backtest.
"""
from __future__ import annotations

import math


def _values(history) -> list:
    """Ordered, positive portfolio values. Malformed points are skipped."""
    out = []
    for point in (history or []):
        if not isinstance(point, dict):
            continue
        try:
            value = float(point.get("value") or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0 and math.isfinite(value):
            out.append(value)
    return out


def max_drawdown(history) -> dict:
    """True peak-to-trough decline, and where it happened.

    Walks forward tracking the running peak, so the trough is only ever
    compared against a peak that PRECEDED it — the distinction
    `portfolio_value_high`/`_low` cannot make.

    Returns {"max_drawdown_pct", "peak_value", "trough_value", "peak_index",
    "trough_index"}; the pct is a positive magnitude (0.0 for a curve that
    never declines). Empty/short input gives an all-None dict rather than 0.0,
    because "no drawdown" and "not measured" must not read the same to a
    promotion gate.
    """
    values = _values(history)
    if len(values) < 2:
        return {"max_drawdown_pct": None, "peak_value": None,
                "trough_value": None, "peak_index": None, "trough_index": None}

    peak = values[0]
    peak_i = 0
    best = {"max_drawdown_pct": 0.0, "peak_value": peak, "trough_value": peak,
            "peak_index": 0, "trough_index": 0}
    for i, value in enumerate(values):
        if value > peak:
            peak, peak_i = value, i
            continue
        decline = (peak - value) / peak
        if decline > best["max_drawdown_pct"]:
            best = {"max_drawdown_pct": decline, "peak_value": peak,
                    "trough_value": value, "peak_index": peak_i,
                    "trough_index": i}
    return best


def period_returns(history) -> list:
    """Simple point-to-point returns of the curve."""
    values = _values(history)
    return [(b - a) / a for a, b in zip(values, values[1:]) if a > 0]


def volatility(history) -> float | None:
    """Sample standard deviation of the point-to-point returns."""
    rets = period_returns(history)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def downsample(history, max_points: int = 512) -> list:
    """Even-stride sample of the curve, first and last always retained.

    The row has to stay a reasonable size; an hourly year is ~1,750 points and
    a promotion review needs the SHAPE plus exact endpoints, not every bar.
    """
    points = [p for p in (history or []) if isinstance(p, dict)]
    if max_points <= 0 or len(points) <= max_points:
        return list(points)
    stride = len(points) / float(max_points)
    picked = [points[min(len(points) - 1, int(i * stride))] for i in range(max_points)]
    if picked[0] is not points[0]:
        picked[0] = points[0]
    if picked[-1] is not points[-1]:
        picked[-1] = points[-1]
    return picked


def risk_metrics(history, max_curve_points: int = 512) -> dict:
    """The block the result row should carry so the gate can be evaluated."""
    values = _values(history)
    dd = max_drawdown(history)
    return {
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "max_drawdown_peak_value": dd["peak_value"],
        "max_drawdown_trough_value": dd["trough_value"],
        "return_volatility": volatility(history),
        "observation_count": len(values),
        "equity_curve": downsample(history, max_curve_points),
    }
