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


DEFAULTS = {
    "strategy_xs_enabled": False,
    # ── the levered core ──
    "core_bull_symbol": "TQQQ",
    "core_leverage_factor": 3.0,
    # Share of the RESIDUAL held in the levered leg; the rest is cash. 0.82 of
    # a 0.55 residual is 45.1% of NAV, i.e. 135% equity beta.
    "core_weight": 0.82,
    "core_cash_symbol": "BIL",
    "core_band_pct": 0.05,
    # ── the filter, imported from strategy_x and unchanged ──
    "core_filter_symbol": "QQQ",
    "core_filter_ma_bars": 200,
    "core_vol_bars": 20,
    "core_vol_slow_bars": 60,
    "core_vol_gate_mult": 2.25,
    "core_vol_median_bars": 252,
    "core_vol_median_min_samples": 60,
    "core_vol_target": 0.0,
    "core_vol_scale_min": 0.3,
    "core_vol_scale_max": 1.0,
    # ── the diversifier, ALWAYS ON ──
    # It is the return source, not a panic button. Measured 2011-2026 on the
    # three years every Strategy X configuration loses money:
    #   asset  CAGR   corr    2015    2018    2022
    #   UUP    2.49  -0.16    +7.0    +7.0    +9.5   <- the only 15y asset
    #   GLD    7.42  +0.06   -10.7    -1.9    -0.8      positive in all three
    #   DBMF   9.21  +0.19     n/a     n/a   +21.6
    #   TLT    2.13  -0.27    -1.8    -1.6   -31.2   <- excluded: 2022
    #   VIXY -48.94  -0.79   -36.5   +66.8   -25.0   <- excluded: carry
    # The dollar cannot carry the sleeve alone at a 2.5% CAGR; gold supplies
    # the long-run return and managed futures the crisis alpha.
    "diversifier_pct": 0.45,
    "diversifier_symbols": ["GLD", "UUP", "DBMF"],
    "diversifier_min_history_bars": 60,
    # ── the Graph stock sleeve (OFF) ──
    # 0.20 arms it, and arming it DE-LEVERS the core from 135% beta to 106%:
    # it swaps levered index beta for stock-picking beta rather than stacking
    # both. Default 0 because this repo has measured Graph Nexus to have no
    # cross-sectional skill — Spearman IC of nexus_base_score against forward
    # returns is negative in every window tested.
    "satellite_pct": 0.0,
    "satellite_max_names": 6,
    # ── the inverse sleeve (OFF, and the numbers are the argument) ──
    # Gated on the same trend filter, it DOES deliver net-positive bears on
    # 7.3 years containing one bear: SQQQ 20% takes 2022 from -7.1% to +0.8%
    # and losing years from 1 to 0. Over the full fifteen years it is
    # monotonically destructive:
    #   sleeve      CAGR   maxDD  negYrs
    #   none       16.13  -24.47     3
    #   PSQ 20%    14.97  -28.08     5
    #   SQQQ 20%   12.43  -35.28     5
    # Worse return, worse drawdown, MORE losing years. It fits the one bear in
    # the short window. There is no setting in between: gate it tighter and it
    # stops firing (Strategy X's kicker engaged twice in 1,258 sessions for a
    # net +$4.29); loosen it and it bleeds in every whipsaw.
    "inverse_symbol": "",
    "inverse_pct": 0.0,
    # ── execution ──
    "min_order_usd": 50.0,
    "cost_haircut_pct": 0.006,
}


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


def _share(budget: float, count: int) -> float:
    """Floor to the grid, never round. Rounding each share UP breaches the
    budget — 0.5/3 rounded to 6dp three times is 0.500001 — and a weight set
    summing past 1.0 asks for a clip the account cannot fund."""
    scale = 10 ** Q
    return math.floor(budget / count * scale) / scale


def xs_targets(*, risk_on: bool, config, basket=(), satellite_ranked=None,
               vol_scale: float = 1.0) -> tuple:
    """Target weight per symbol as a fraction of NAV, plus why.

    Named sleeves are paid first and the core is the RESIDUAL. The alternative
    — sizing the core first — is what let an unfilled sleeve raise leverage in
    bt 773215, where bar 1 went 80% TQQQ instead of the designed 60% and TQQQ
    alone accounted for 133% of the loss.
    """
    cfg = config or {}
    notes: list = []
    targets: dict = {}

    bull = _s(cfg, "core_bull_symbol")
    cash = _s(cfg, "core_cash_symbol")
    inverse = _s(cfg, "inverse_symbol", "")

    sat_pct = max(0.0, min(1.0, _f(cfg, "satellite_pct")))
    div_pct = max(0.0, min(1.0, _f(cfg, "diversifier_pct")))
    # The residual is sized off the DESIGNED sleeve budgets, so core leverage
    # is the same whether or not a sleeve fills.
    residual = round(max(0.0, 1.0 - sat_pct - div_pct), Q)

    # ── the diversifier, in both regimes ──
    members = [str(s).strip().upper() for s in (basket or ()) if s]
    members = list(dict.fromkeys(members))
    if div_pct > 0 and members:
        each = _share(div_pct, len(members))
        for symbol in members:
            targets[symbol] = each
        spent = round(each * len(members), Q)
        notes.append(f"diversifier {spent:.0%} across {len(members)}: "
                     + ", ".join(members))
    else:
        spent = 0.0
        if div_pct > 0:
            notes.append("no diversifier member qualifies — weight to cash")
    div_short = round(div_pct - spent, Q)

    # ── the Graph stock sleeve ──
    names = [str(s).strip().upper() for s in (satellite_ranked or []) if s]
    names = [s for s in dict.fromkeys(names)
             if s not in targets and s not in (bull, cash, inverse)]
    names = names[:max(0, _i(cfg, "satellite_max_names"))]
    if sat_pct > 0 and names:
        each = _share(sat_pct, len(names))
        for symbol in names:
            targets[symbol] = each
        sat_spent = round(each * len(names), Q)
        notes.append(f"graph {sat_spent:.0%} across {len(names)}")
    else:
        sat_spent = 0.0
        if sat_pct > 0:
            notes.append("no graph name ranked — sleeve holds cash")
    sat_short = round(sat_pct - sat_spent, Q)

    # Every shortfall goes to CASH, never to the levered core.
    idle = round(div_short + sat_short, Q)

    if risk_on:
        weight = max(0.0, min(1.0, _f(cfg, "core_weight")))
        # Clamped to [0, 1] and fail-safe: a malformed scale must never RAISE
        # exposure above what the operator configured.
        try:
            scale = float(vol_scale)
        except (TypeError, ValueError):
            scale = 1.0
        if not math.isfinite(scale):
            scale = 1.0
        scale = max(0.0, min(1.0, scale))
        targets[bull] = round(residual * weight * scale, Q)
        rest = round(residual - targets[bull] + idle, Q)
        if rest > 0:
            targets[cash] = round(targets.get(cash, 0.0) + rest, Q)
        notes.append(f"risk-on: {targets[bull]:.1%} {bull}"
                     + (f" | vol scale {scale:.2f}" if scale < 1.0 else ""))
        return targets, notes

    # ── risk-off: the core goes to CASH, not to the unlevered index ──
    # This is the whole difference from Strategy X, whose de-levered weight
    # lands in SPY — so a nominal 70% TQQQ book is really 27% TQQQ and 57% SPY,
    # and its measured result therefore tracks SPY.
    inv_pct = max(0.0, min(1.0, _f(cfg, "inverse_pct")))
    parked = round(residual + idle, Q)
    if inverse and inv_pct > 0 and inv_pct <= parked:
        targets[inverse] = inv_pct
        parked = round(parked - inv_pct, Q)
        notes.append(f"risk-off: {inv_pct:.1%} {inverse} (inverse sleeve ARMED)")
    if parked > 0:
        targets[cash] = round(targets.get(cash, 0.0) + parked, Q)
    notes.append(f"risk-off: {targets.get(cash, 0.0):.1%} {cash}")
    return targets, notes


def strategy_xs_universe(config) -> list:
    """Every symbol this strategy can trade or read, deterministic order.

    The strategy owns its universe rather than depending on the instance's
    watchlist. Without this the filter symbol has no bars and the traded legs
    have no price, and BOTH failures are silent — the strategy simply emits
    nothing. `broker._strategy_x_prepare` reads this to decide what to fetch.
    """
    cfg = config or {}
    syms = [_s(cfg, "core_filter_symbol"), _s(cfg, "core_bull_symbol"),
            _s(cfg, "core_cash_symbol")]
    if _f(cfg, "diversifier_pct") > 0:
        syms += [str(s).strip().upper()
                 for s in (cfg.get("diversifier_symbols") or []) if s]
    if _s(cfg, "inverse_symbol", "") and _f(cfg, "inverse_pct") > 0:
        syms.append(_s(cfg, "inverse_symbol", ""))
    seen, out = set(), []
    for s in syms:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
