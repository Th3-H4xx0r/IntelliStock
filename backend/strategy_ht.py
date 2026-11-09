"""Strategy HT (hysteresis trend): the pure rule, no broker, no I/O.

Rule (pre-registered 2026-11-08, .claude/handoffs/sweep-2026-11-08/STRATEGY_hysteresis_trend_sso.md):
  ENTER the market state when the filter close > (1 + enter_band) x SMA(ma_bars).
  EXIT  to the parked state when the filter close < (1 - exit_band) x SMA(ma_bars).
  Between the bands the state is KEPT (hysteresis). Before the first decisive
  crossing the state is unknown and treated as parked (fail closed).
  In state  : `in_weight` of NAV in `in_symbol` (default 100% SSO).
  Out state : `out_symbols` weights (default 50% SPY, 50% BIL).

Offline evidence (same directory): modelled 2x 1951-2012 CAGR 15.4% vs 9.1%,
2013-2026 22.1% vs 14.5%; real SSO/SPY/BIL 2006-2026 16.2% vs 11.0%, maxDD
-45% vs -55%, ~1.1 switches/yr. It beats SPY in ~6 of 10 years and has 20-30%
down years (2008, 2022); it is NOT an every-year rule.

The state is derived by replaying the band rule over the visible close series,
so it needs no cache and cannot drift from the closes it was computed from.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Q = 10  # rounding places for byte-stable arithmetic across platforms

DEFAULTS = {
    "strategy_ht_enabled": False,
    # ── the filter ──
    "filter_symbol": "SPY",
    "ma_bars": 200,
    # Asymmetric bands (Balanced profile, 2026-11-08): re-enter quickly, exit late.
    # Measured on real ETFs 2006-2026 vs the symmetric 3%/3% base: +2pp CAGR,
    # because the 1% re-entry gets back in months earlier after a bottom.
    "enter_band": 0.01,
    "exit_band": 0.05,
    # ── in-state sizing ──
    # Volatility target on the in-state exposure: exposure = clip(vol_target /
    # realised vol(vol_bars), exp_min, exp_max). 0 disables it (always exp_max).
    # 20% cut the worst year from -31% to -18% and maxDD -45% -> -39% on real
    # ETFs, for ~2.5pp of CAGR. Exposure e is held as SSO (e-1) + SPY (2-e)
    # when e >= 1, and SPY e + BIL (1-e) when e < 1.
    "vol_target": 0.20,
    "vol_bars": 21,
    "exp_min": 0.5,
    "exp_max": 2.0,
    # Exposure is quantised to this grid (0.5 -> 0.5x/1x/1.5x/2x), so the book
    # only trades when realised vol moves a regime, not every day. Measured on
    # the engine: the continuous version rebalanced on 320 of 512 sessions,
    # left ~9% of NAV idle in cash on average and returned +38% where the
    # offline model said +84%; the 0.5 grid trades ~16 days/yr offline with
    # the same CAGR (ht_variants_lowturn.txt). 0 = continuous.
    "exp_grid": 0.5,
    # Reflex bounce: the day after a close <= bounce_drop, hold exp_max
    # regardless of state (up 23 of 30 times since 1950). 0 disables it.
    "bounce_drop": -0.05,
    # ── the legs ──
    "in_symbol": "SSO",          # the 2x fund
    "one_x_symbol": "SPY",       # the 1x leg used to dial exposure between 0.5x and 2x
    "cash_symbol": "BIL",
    "in_weight": 1.0,            # legacy cap on the in-state book (1.0 = full)
    "out_symbols": {"SPY": 0.5, "BIL": 0.5},
    # ── order plumbing (read by strategy_x.targets_to_orders) ──
    "core_band_pct": 0.05,          # no-churn drift band around a held target
    "min_order_usd": 50.0,
    "cost_haircut_pct": 0.006,
    # The broker's 15% single-position failsafe would trim a 100% SSO buy to
    # $0.00 (BT102936; how Strategy XS first shipped inert). The engine honours
    # a wider cap only for lanes that opt in with BOTH keys.
    "broker_max_single_position_pct": 1.0,
    "honour_single_position_cap": True,
}


def _f(cfg, key, default=None):
    """Float from config; anything unparseable or non-finite -> DEFAULTS[key]."""
    fallback = DEFAULTS.get(key) if default is None else default
    try:
        value = float((cfg or {}).get(key, fallback))
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(value):
        return float(fallback)
    return value


def _i(cfg, key):
    try:
        value = float((cfg or {}).get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return int(DEFAULTS[key])
    if not math.isfinite(value):
        return int(DEFAULTS[key])
    return int(value)


def _s(cfg, key, default=None):
    fallback = DEFAULTS.get(key, "") if default is None else default
    raw = (cfg or {}).get(key, fallback)
    text = str(raw if raw is not None else fallback).strip().upper()
    return text or str(fallback).strip().upper()


def out_book(cfg) -> dict:
    """`out_symbols` as {SYMBOL: weight}, weights clamped to [0, 1] and the
    whole book scaled down to 1.0 if it was configured above it. A malformed
    book falls back to DEFAULTS. Never raises exposure above 100%."""
    raw = (cfg or {}).get("out_symbols", DEFAULTS["out_symbols"])
    book: dict = {}
    if isinstance(raw, dict):
        for sym, w in raw.items():
            try:
                weight = float(w)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(weight) or weight <= 0:
                continue
            key = str(sym or "").strip().upper()
            if key:
                book[key] = round(book.get(key, 0.0) + min(1.0, weight), Q)
    if not book:
        book = dict(DEFAULTS["out_symbols"])
    total = sum(book.values())
    if total > 1.0:
        book = {s: round(w / total, Q) for s, w in book.items()}
    return book


def strategy_ht_universe(cfg) -> list:
    """Every symbol the lane needs bars and prices for, sorted."""
    syms = ({_s(cfg, "filter_symbol"), _s(cfg, "in_symbol"), _s(cfg, "one_x_symbol"),
             _s(cfg, "cash_symbol")} | set(out_book(cfg)))
    return sorted(s for s in syms if s)


@dataclass(frozen=True)
class TrendState:
    in_market: bool
    reason: str
    price: float = 0.0
    ma: float = 0.0
    decided: bool = False      # False = no decisive crossing yet (parked)
    crossings: int = 0


def _finite(closes) -> list | None:
    out = []
    for value in closes or []:
        try:
            px = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(px) or px <= 0:
            return None
        out.append(px)
    return out


def trend_state(closes, config) -> TrendState:
    """Replay the hysteresis band over `closes` (oldest first).

    Fails CLOSED: too little history, a bad close, or no decisive crossing in
    the visible window all return in_market=False.
    """
    cfg = config or {}
    ma_bars = max(2, _i(cfg, "ma_bars"))
    enter = max(0.0, _f(cfg, "enter_band"))
    exit_ = max(0.0, _f(cfg, "exit_band"))

    px = _finite(closes)
    if px is None:
        return TrendState(False, "non-finite or non-positive close in the series")
    if len(px) < ma_bars:
        return TrendState(False, f"insufficient history: {len(px)} closes < {ma_bars}")

    # rolling mean by cumulative sum, rounded for byte-stable comparisons
    csum = [0.0]
    for value in px:
        csum.append(csum[-1] + value)
    state = None
    crossings = 0
    last_price = last_ma = 0.0
    for i in range(ma_bars - 1, len(px)):
        ma = round((csum[i + 1] - csum[i + 1 - ma_bars]) / ma_bars, Q)
        price = round(px[i], Q)
        last_price, last_ma = price, ma
        if ma <= 0:
            continue
        ratio = price / ma
        if ratio > 1.0 + enter:
            if state is not True:
                crossings += 1
            state = True
        elif ratio < 1.0 - exit_:
            if state is not False:
                crossings += 1
            state = False
    if state is None:
        return TrendState(False, "no decisive crossing yet (parked, fail-closed)",
                          last_price, last_ma, False, 0)
    pct = (last_price / last_ma - 1.0) * 100 if last_ma > 0 else 0.0
    word = "IN" if state else "OUT"
    return TrendState(bool(state), f"{word}: price {last_price:.2f} is {pct:+.2f}% vs "
                      f"MA{ma_bars} {last_ma:.2f} (bands +{enter:.0%}/-{exit_:.0%})",
                      last_price, last_ma, True, crossings)


def realised_vol(closes, bars: int) -> float | None:
    """Annualised standard deviation of the last `bars` daily returns, or None."""
    px = _finite(closes)
    if px is None or len(px) < bars + 1:
        return None
    rets = [px[i] / px[i - 1] - 1.0 for i in range(len(px) - bars, len(px))]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    return math.sqrt(var) * math.sqrt(252.0)


def in_exposure(closes, config) -> tuple:
    """In-state exposure in units of the index (0.5 .. 2.0), plus why.

    vol_target / realised vol, clipped to [exp_min, exp_max]. Fails toward
    exp_max only when the target is off or the vol is zero/unknown with enough
    history; too little history for the vol window falls to exp_min (less).
    The bounce overlay returns exp_max the day after a close <= bounce_drop.
    """
    cfg = config or {}
    lo = max(0.0, _f(cfg, "exp_min")); hi = max(lo, _f(cfg, "exp_max"))
    px = _finite(closes) or []
    drop = _f(cfg, "bounce_drop")
    if drop < 0 and len(px) >= 2 and px[-1] / px[-2] - 1.0 <= drop:
        return hi, f"bounce: last day {100 * (px[-1] / px[-2] - 1.0):+.1f}% <= {100 * drop:.0f}% -> {hi:.2f}x"
    target = _f(cfg, "vol_target")
    if target <= 0:
        return hi, f"vol target off -> {hi:.2f}x"
    bars = max(2, _i(cfg, "vol_bars"))
    vol = realised_vol(px, bars)
    if vol is None:
        return lo, f"insufficient history for vol({bars}) -> {lo:.2f}x (fail-closed)"
    if vol <= 0:
        return hi, f"zero realised vol -> {hi:.2f}x"
    raw = target / vol
    grid = _f(cfg, "exp_grid")
    if grid > 0:
        raw = round(raw / grid) * grid
    exp = round(max(lo, min(hi, raw)), 4)
    return exp, f"vol {100 * vol:.1f}% vs target {100 * target:.0f}% -> {exp:.2f}x"


def exposure_targets(exposure: float, config) -> dict:
    """Weights that deliver `exposure` with a 2x fund, a 1x leg and cash."""
    cfg = config or {}
    e = max(0.0, min(2.0, float(exposure)))
    two_x, one_x, cash = _s(cfg, "in_symbol"), _s(cfg, "one_x_symbol"), _s(cfg, "cash_symbol")
    targets: dict = {}
    if e >= 1.0:
        if e - 1.0 > 0:
            targets[two_x] = round(e - 1.0, Q)
        if 2.0 - e > 0:
            targets[one_x] = round(targets.get(one_x, 0.0) + (2.0 - e), Q)
    else:
        if e > 0:
            targets[one_x] = round(e, Q)
        targets[cash] = round(targets.get(cash, 0.0) + (1.0 - e), Q)
    return targets


def ht_targets(in_market: bool, config, exposure: float | None = None) -> tuple:
    """Target weight per symbol as a fraction of NAV, plus why.

    `exposure` is the in-state exposure from `in_exposure`; None means exp_max
    (the pure 2x book). `in_weight` < 1 scales the in-state book toward cash.
    """
    cfg = config or {}
    if in_market:
        hi = max(0.0, _f(cfg, "exp_max"))
        e = hi if exposure is None else max(0.0, min(hi, float(exposure)))
        weight = max(0.0, min(1.0, _f(cfg, "in_weight")))
        targets = {s: round(w * weight, Q) for s, w in exposure_targets(e, cfg).items()}
        targets = {s: w for s, w in targets.items() if w > 0}
        notes = [f"in-market {e:.2f}x: " + ", ".join(f"{w:.0%} {s}" for s, w in sorted(targets.items()))]
        return targets, notes
    book = out_book(cfg)
    notes = ["parked: " + ", ".join(f"{w:.0%} {s}" for s, w in sorted(book.items()))]
    return dict(book), notes
