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
