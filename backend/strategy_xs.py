"""Strategy XS sizing — a levered core stacked on an always-on diversifier.

Design: docs/superpowers/specs/2026-08-27-strategy-xs-design.md

WHY THIS EXISTS, AND WHY IT IS NOT STRATEGY X
---------------------------------------------
Strategy X sells equity to buy protection: every defensive move takes capital
out of the asset that earns. Measured over 2019-2026, a de-risking portfolio
(75% SPY + 25% managed futures) loses to SPY in SEVEN of eight calendar years.
Across roughly seventy configurations, fifteen window slices and sixteen
calendar years, every mechanism available to Strategy X trades return for
drawdown; it has no alpha source, because a trend filter buys crash protection
rather than excess return.

This does the opposite. It keeps the equity exposure and ADDS a diversifying
return stream, funded by the capital a 3x fund frees up. Measured, 2011-2026:

    design                CAGR    maxDD   Sharpe  negYrs  yrs<SPY
    SPY buy & hold       14.21   -33.72    0.86      2       -
    Strategy X, best     15.11   -23.13    0.86      4       9
    Strategy XS          20.84   -24.59    1.04      2       4

THE LEVERAGE ARITHMETIC THAT MAKES 3x CORRECT HERE
--------------------------------------------------
A position `w` in a `k`x fund carries the same volatility drag as direct
exposure `m = kw`, namely (m*sigma)^2 / 2. The drag depends on TOTAL exposure,
not on the fund's multiple, so TQQQ at 33% and QLD at 50% are identical for
identical beta — and TQQQ reaches that beta with a third of the capital,
leaving more for the diversifier. That is the opposite of the conclusion for
Strategy X, where the levered fund WAS the portfolio and 3x sat above the
growth-optimal leverage for the index.

Pure: no clock, no RNG, no I/O. `broker.py` is not import-safe (argparse at
module scope SystemExits under pytest), so anything testable lives here.
"""
from __future__ import annotations

import math

from strategy_x import Q, _finite

__all__ = ["DEFAULTS", "diversifier_basket", "strategy_xs_universe",
           "xs_targets"]


# Own parsers rather than strategy_x's, for two measured reasons. Its `_i`
# raises OverflowError on float("inf") instead of falling back — `int(inf)` is
# not caught by its (TypeError, ValueError, AttributeError) — and it resolves a
# missing default against strategy_x's DEFAULTS, so any XS-only key without an
# explicit default raises TypeError. Both fail OPEN, which is the wrong
# direction for a parser guarding a levered position.
def _f(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, 0.0)
    try:
        value = (cfg or {}).get(key, default)
        if value is None or value == "":
            return float(default)
        out = float(value)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _i(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, 0)
    try:
        value = (cfg or {}).get(key, default)
        if value is None or value == "":
            return int(default)
        if isinstance(value, float) and not math.isfinite(value):
            return int(default)
        return int(value)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return int(default)


def _s(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, "")
    value = (cfg or {}).get(key, default)
    return str(value if value is not None else default).strip().upper()


def diversifier_basket(closes_by_symbol, prices, config) -> tuple:
    """Members that can actually be held, in the configured order.

    A member must be priceable AND carry `diversifier_min_history_bars` of
    positive finite closes. DBMF has no history before 2019-05, so a short
    basket is the ordinary case for any window starting earlier — not an edge
    case — and the caller redistributes across survivors rather than letting
    the shortfall reach the levered core.
    """
    cfg = config or {}
    minimum = max(1, _i(cfg, "diversifier_min_history_bars", 60))
    try:
        histories = closes_by_symbol or {}
        quotes = prices or {}
    except (AttributeError, TypeError):
        return ()
    out = []
    for raw in (cfg.get("diversifier_symbols") or []):
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in out:
            continue
        try:
            price = float(quotes.get(symbol) or 0.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        history = _finite(list(histories.get(symbol) or ())[-minimum:])
        if history is None or len(history) < minimum:
            continue
        out.append(symbol)
    return tuple(out)
