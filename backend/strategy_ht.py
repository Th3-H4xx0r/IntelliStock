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
    "enter_band": 0.03,
    "exit_band": 0.03,
    # ── the two books ──
    "in_symbol": "SSO",
    "in_weight": 1.0,
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
    syms = {_s(cfg, "filter_symbol"), _s(cfg, "in_symbol")} | set(out_book(cfg))
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


def ht_targets(in_market: bool, config) -> tuple:
    """Target weight per symbol as a fraction of NAV, plus why."""
    cfg = config or {}
    if in_market:
        weight = max(0.0, min(1.0, _f(cfg, "in_weight")))
        sym = _s(cfg, "in_symbol")
        targets = {sym: round(weight, Q)}
        notes = [f"in-market: {weight:.0%} {sym}"]
        return targets, notes
    book = out_book(cfg)
    notes = ["parked: " + ", ".join(f"{w:.0%} {s}" for s, w in sorted(book.items()))]
    return dict(book), notes
