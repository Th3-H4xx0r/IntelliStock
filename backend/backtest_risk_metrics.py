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


def sleeve_churn(trades, sleeve_symbols) -> dict:
    """How much the index core traded to achieve how little net change.

    The core is a RESIDUAL of the satellite -- core = clamp(1 - cash_floor -
    satellite, min, max) -- so every satellite move retargets it. Concentrating
    the satellite into 14%-of-NAV positions swings that target 14 points at a
    time, and the core trades to follow. Its funding RELEASE is deliberately
    exempt from the rebalance cadence (it exists to fund an approved buy), so
    the cadence that bounds the deploy side does not bound it.

    Measured on bt 820236: 26 SPY fills, 17 side-flips, sources
    residual_bull_refill x17 and residual_bull_deploy x9 -- $11,186 of
    post-initial gross around a $2,398 core. Turnover is the objective's named
    leak (~50%/mo break-even, 23-35% for this edge), and a saw-tooth on the one
    lane exempt from the turnover budget is the most expensive place to have it.

    This measures; it changes no behaviour. `churn_ratio` is gross traded per
    dollar of NET position change -- 1.0 is a clean directional move, and the
    larger it gets the more of the trading achieved nothing. None when there is
    no net change to divide by (a perfect round trip), which is the pathological
    case and must not read as 0.
    """
    syms = {str(s).strip().upper() for s in (sleeve_symbols or set())}
    fills = []
    for t in (trades or []):
        if not isinstance(t, dict):
            continue
        if str(t.get("ticker") or "").strip().upper() not in syms:
            continue
        try:
            total = abs(float(t.get("total") or 0.0))
        except (TypeError, ValueError):
            continue
        action = str(t.get("action") or "").strip().lower()
        if total <= 0 or action not in {"buy", "sell"}:
            continue
        fills.append((action, total))

    if not fills:
        return {"fill_count": 0, "gross_notional": 0.0, "net_notional": 0.0,
                "churn_ratio": None, "side_flips": 0}

    gross = sum(total for _a, total in fills)
    net = sum(total if a == "buy" else -total for a, total in fills)
    flips = sum(1 for x, y in zip(fills, fills[1:]) if x[0] != y[0])
    # The opening deployment is a directional move, not churn; exclude it so the
    # ratio describes what happened AFTER the book was built.
    post_initial_gross = gross - fills[0][1]
    return {
        "fill_count": len(fills),
        "gross_notional": gross,
        "post_initial_gross_notional": post_initial_gross,
        "net_notional": net,
        "churn_ratio": (gross / abs(net)) if abs(net) > 1e-9 else None,
        "side_flips": flips,
    }


def sleeve_symbols_from_schema(strategy_schema) -> set:
    """The sleeve's own legs, read off a stored strategy_schema.

    The result row keeps the schema, so churn can be attributed without the
    caller re-deriving which tickers are broker-level cash parking rather than
    strategy positions.
    """
    out: set = set()
    try:
        for spec in ((strategy_schema or {}).get("strategies") or []):
            cfg = (spec or {}).get("config") or {}
            if not bool(cfg.get("residual_sleeve_enabled", False)):
                continue
            for key, default in (("residual_sleeve_symbol", "SPY"),
                                 ("residual_sleeve_bear_symbol", "")):
                sym = str(cfg.get(key, default) or "").strip().upper()
                if sym:
                    out.add(sym)
    except Exception:
        return out
    return out
