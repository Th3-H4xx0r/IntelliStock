"""Strategy HX sizing — fast-in, slow-out bear hedge on a vol-targeted core.

Design: docs/superpowers/specs/2026-09-10-strategy-hx-design.md

Two of the three canonical bear windows are TWO MONTHS long. No slow trend
filter can be positive inside one, which is why Strategy EB's inverse line —
gated on a 25-session weekly filter — has never passed. HX tests the one
construction that can react in days: a fast entry into a bear book and a slow,
hysteresis-gated exit. It contains NO exotic signal; the 34-claim audit of
2026-09-10 found nothing retail-viable this engine can express. It is a risk
transform on the only thing that has survived here, a volatility-targeted
leveraged index core, imported from strategy_eb unchanged.

Pure: no clock, no RNG, no I/O. `broker.py` is not import-safe (argparse at
module scope SystemExits under pytest), so anything testable lives here.
"""
from __future__ import annotations

import math

from strategy_eb import eb_core_weight
from strategy_x import Q, _finite

__all__ = ["DEFAULTS", "hx_state", "hx_targets", "strategy_hx_universe"]


DEFAULTS = {
    "strategy_hx_enabled": False,
    # Volatility is measured on the UNLEVERED index; measuring it on TQQQ
    # would divide a 3x-inflated vol by the 3x leverage a second time.
    "reference_symbol": "QQQ",
    "core_symbol": "TQQQ",
    "core_leverage": 3.0,
    # ── the core transform, identical to the EB champion's numbers ──
    "target_vol": 0.20,
    "core_max_weight": 0.65,
    "weight_step": 0.05,
    "vol_fast_bars": 10,
    "vol_slow_bars": 40,
    # 70 covers all three consumers: the 40-bar slow vol window needs 41, the
    # 50-bar SMA compared against itself 20 sessions back needs 70, and the
    # 20-session low with a 10-session drawdown scan needs 30.
    "min_history_bars": 70,
    "bull_remainder_symbol": "QQQ",
    "cash_symbol": "BIL",
    "chop_core_damp": 0.5,
    # {SYMBOL: fraction of NAV}. Five preregistered variants; this is V1.
    "bear_book": {"PSQ": 0.60, "BIL": 0.40},
    # ── the state machine. The asymmetry IS the design: one session enters
    # BEAR, five consecutive closes above the average leave it. ──
    "fast_low_bars": 20,
    "sma_bars": 50,
    "drawdown_pct": 0.07,
    "drawdown_bars": 10,
    "exit_confirm_sessions": 5,
    "chop_slope_bars": 20,
    "chop_slope_pct": 0.015,
    # Passed to targets_to_orders as `core_band_pct`. A state change
    # rebalances the full book regardless of it; within a state it is what
    # keeps the book from trading.
    "rebalance_band": 0.10,
    "min_order_usd": 25.0,
    # ── broker-side, read by engines/backtest_engine.py, not by this module ──
    "honour_single_position_cap": True,
    # DEAD KEY, kept because the design brief names it. broker.py lists it in
    # `_DEAD_STRATEGY_CONFIG_KEYS` and warns on boot; that warning documents
    # the trap in the log rather than only in a comment.
    "max_single_position_pct": 0.95,
    # THE key that works. `_instance_single_position_pct` reads exactly this
    # name off any lane setting `honour_single_position_cap` and forwards it
    # as BROKER_MAX_SINGLE_POSITION_PCT. Without it the 15% failsafe trims a
    # 65%-of-NAV core buy to $0.00 and holds whatever it had — BT102936, and
    # Strategy XS shipped inert the same way.
    "broker_max_single_position_pct": 0.95,
}

#: The keys `eb_core_weight` reads, named explicitly rather than passing the
#: whole HX config through. They coincide today; naming them means a later HX
#: rename breaks a test instead of silently resizing a 3x position.
_EB_WEIGHT_KEYS = ("core_leverage", "target_vol", "core_max_weight",
                   "weight_step", "vol_fast_bars", "vol_slow_bars",
                   "min_history_bars")


# Own parsers rather than strategy_x's: its `_i` raises OverflowError on
# float("inf"), and it resolves a missing default against strategy_x's
# DEFAULTS, so any HX-only key without one raises TypeError. Both fail OPEN,
# the wrong direction for a parser guarding a levered position.
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


_STATES = frozenset({"UNKNOWN", "BULL", "CHOP", "BEAR"})


def _bars(cfg, key):
    """A window/counter length, read as its documented default when unusable.

    The parsers deliberately do not clamp ranges, so a 0 or a negative reaches
    the state machine intact. Floored with `max(1, n)` a corrupted
    `exit_confirm_sessions` of 0 becomes a ONE-session bear exit — four
    sessions early on a 3x fund — and a corrupted `sma_bars` drags the history
    floor it feeds down with it. A non-positive length is not a shorter
    window, it is a MISSING one.
    """
    n = _i(cfg, key)
    if n > 0:
        return n
    fallback = _i({}, key)
    return fallback if fallback > 0 else 1


def _sma(values, bars):
    if bars <= 0 or len(values) < bars:
        return None
    return sum(values[-bars:]) / float(bars)


def _entered_bear(prices, cfg) -> bool:
    """The FAST leg. Either rule alone fires; both are one-session tests.

    Rule A — a 20-session low that is ALSO below the 50-session average. A new
    low alone happens in every healthy pullback.

    Rule B — a `drawdown_pct` fall from the highest close of the prior
    `fast_low_bars` sessions, where that high is at most `drawdown_bars`
    sessions old. The AGE test is load-bearing: scanning the last ten sessions
    for any historical 7% drop instead re-fires on stale crash lows the
    session after a confirmed exit, which cancels the 5-session hysteresis and
    turns the slow leg into a no-op.
    """
    low_bars = max(2, _bars(cfg, "fast_low_bars"))
    dd_bars = max(1, _bars(cfg, "drawdown_bars"))
    dd_pct = max(0.0, min(1.0, _f(cfg, "drawdown_pct")))
    sma_bars = max(2, _bars(cfg, "sma_bars"))
    close = prices[-1]

    sma = _sma(prices, sma_bars)
    if sma is not None and len(prices) > low_bars:
        prior = prices[-(low_bars + 1):-1]
        if close < min(prior) and close < sma:
            return True

    window = prices[-(low_bars + 1):]
    peak = max(window)
    if peak <= 0:
        return False
    age = len(window) - 1 - max(i for i, v in enumerate(window) if v == peak)
    return age <= dd_bars and close <= peak * (1.0 - dd_pct)


def hx_state(closes, prev_state, confirm, cfg) -> tuple:
    """(state, confirm counter) for this session.

    Read-only on everything. The caller persists both; passing them back in is
    what makes the exit hysteresis survive a restart, and a cold start reads
    UNKNOWN rather than inventing a regime.

    The exit names its destination — BEAR leaves to BULL, as the spec writes
    the rule — and the BULL/CHOP test applies from the NEXT session. Running
    the CHOP test on the flip session would land a confirmed five-session
    recovery in a damped book on a technicality.
    """
    prices = _finite(closes)
    # Whichever consumer needs most history. Without the window terms a raised
    # `sma_bars` would compare a truncated average against itself, and a
    # shorter window on the same tape measures LESS risk — the one direction
    # this module must never fail in.
    minimum = max(2, _bars(cfg, "min_history_bars"),
                  _bars(cfg, "sma_bars") + _bars(cfg, "chop_slope_bars"),
                  _bars(cfg, "fast_low_bars") + _bars(cfg, "drawdown_bars"))
    if prices is None or len(prices) < minimum:
        return "UNKNOWN", 0

    state = str(prev_state or "").strip().upper()
    if state not in _STATES:
        state = "UNKNOWN"
    try:
        count = int(confirm)
    except (TypeError, ValueError, OverflowError):
        count = 0
    # Bounded like every other parsed counter in this repo, so a corrupted row
    # cannot carry an unbounded integer into the comparison below.
    count = max(0, min(100_000, count))

    sma_bars = max(2, _bars(cfg, "sma_bars"))
    sma = _sma(prices, sma_bars)
    if sma is None or not math.isfinite(sma) or sma <= 0:
        return "UNKNOWN", 0
    close = prices[-1]

    if state == "BEAR":
        need = max(1, _bars(cfg, "exit_confirm_sessions"))
        count = count + 1 if close > sma else 0
        return ("BULL", 0) if count >= need else ("BEAR", count)

    if _entered_bear(prices, cfg):
        return "BEAR", 0

    slope_bars = max(1, _bars(cfg, "chop_slope_bars"))
    prior_sma = _sma(prices[:-slope_bars], sma_bars)
    # A dead tape closes AT its own average, never below it, so the slope
    # clause is the only thing standing between a flat market and a full 3x
    # core. Unmeasurable slope reads as flat: CHOP is the cheaper error.
    flat = True
    if prior_sma is not None and prior_sma > 0:
        flat = abs(sma / prior_sma - 1.0) < max(0.0, _f(cfg, "chop_slope_pct"))
    if close < sma or flat:
        return "CHOP", 0
    return "BULL", 0


def strategy_hx_universe(cfg) -> list:
    """Every symbol this strategy reads or trades, sorted and de-duplicated.

    The strategy owns its universe rather than depending on the instance's
    watchlist. Without this the reference symbol has no bars and the traded
    legs have no price, and BOTH failures are silent — the strategy just emits
    nothing. `broker._strategy_hx_universe_symbols` reads this to decide what
    to fetch.
    """
    out = {_s(cfg, "reference_symbol"), _s(cfg, "core_symbol"),
           _s(cfg, "bull_remainder_symbol"), _s(cfg, "cash_symbol")}
    raw = (cfg or {}).get("bear_book")
    if isinstance(raw, dict):
        for sym in raw:
            out.add(str(sym or "").strip().upper())
    return sorted(s for s in out if s)
