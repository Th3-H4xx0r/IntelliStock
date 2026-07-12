# INTELLISTOCK_SCHEMA: {"strategy": "Fast", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "high", "breakout_window": 20, "fast_ema": 9, "slow_ema": 21}}
# INTELLISTOCK_DESCRIPTION: Band 1 fast tactical crypto strategy. BTC/USD and ETH/USD only. Short-window breakout confirmed by a fast/slow EMA cross; only trades when the momentum gap clears the maker round-trip cost. Breakdown below the recent range exits to USD.
# DIFFICULTY: 3
"""
Fast tactical crypto strategy (run_once). DB name: "fast" (file fast.py, class Fast).

BTC/ETH only. On the provided (short-timeframe) bars:
- Breakout: close > prior N-bar high AND fast-EMA > slow-EMA AND the EMA
  momentum gap clears ``core.min_edge_to_trade(maker=False)`` (real taker
  round-trip cost, since the adapter places market orders) -> buy (1).
- Breakdown: close < prior N-bar low AND fast-EMA < slow-EMA -> exit (-1).
- Otherwise (chop, or a move too small to beat fees) -> hold (0).
Returns 1 = buy, 0 = hold, -1 = sell, plus ``_nexus_discovered`` for the
BTC/ETH pairs when they were auto-added (seed list empty).
"""

from __future__ import annotations

from typing import Mapping, Optional

import numpy as np
import talib

try:
    import sys
    import os
    _crypto_dir = os.path.dirname(os.path.abspath(__file__))
    _backend_dir = os.path.dirname(os.path.dirname(_crypto_dir))
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
except Exception:
    pass

from strategies.crypto import core

# This band is intentionally scoped to the two most-liquid pairs.
FAST_PAIRS = ["BTC/USD", "ETH/USD"]

# Shared helper (single source of truth in core).
_series = core.series


class Fast:
    """Short-window breakout/momentum on BTC & ETH."""

    def __init__(self):
        self.breakout_window = 20
        self.fast_ema = 9
        self.slow_ema = 21

    def run_once(
        self,
        symbols,
        prices,
        current_time,
        config,
        conditions,
        data=None,
        portfolio_emulator=None,
        strategy_cache=None,
        time_increment=None,
        mode=None,
    ) -> dict:
        settings = {}
        if isinstance(conditions, dict):
            settings.update(conditions)
        if isinstance(config, dict):
            settings.update(config)

        if mode == "IDLE":
            return {}

        window = int(settings.get("breakout_window", self.breakout_window))
        fast_p = int(settings.get("fast_ema", self.fast_ema))
        slow_p = int(settings.get("slow_ema", self.slow_ema))
        window = max(2, window)
        fast_p = max(2, fast_p)
        slow_p = max(fast_p + 1, slow_p)
        edge = core.min_edge_to_trade(maker=False)
        min_bars = slow_p + window + 2

        seed = {str(s).strip().upper() for s in (symbols or [])}
        data = data or {}

        result: dict = {}
        discovered = []
        for sym in FAST_PAIRS:
            bars = data.get(sym)
            if not bars:
                continue
            closes = _series(bars, "c")
            highs = _series(bars, "h")
            lows = _series(bars, "l")
            if len(closes) < min_bars:
                result[sym] = 0
                if sym not in seed:
                    discovered.append(sym)
                continue

            fast_ema = talib.EMA(closes, timeperiod=fast_p)
            slow_ema = talib.EMA(closes, timeperiod=slow_p)
            fe, se = fast_ema[-1], slow_ema[-1]
            if np.isnan(fe) or np.isnan(se) or se <= 0:
                result[sym] = 0
                if sym not in seed:
                    discovered.append(sym)
                continue

            close_now = closes[-1]
            prior_high = float(np.max(highs[-(window + 1):-1]))
            prior_low = float(np.min(lows[-(window + 1):-1]))
            gap = fe / se - 1.0

            if close_now > prior_high and fe > se and gap >= edge:
                score = 1
            elif close_now < prior_low and fe < se:
                score = -1
            else:
                score = 0
            result[sym] = score
            if sym not in seed:
                discovered.append(sym)

        # Exit held coins we can't see this tick — coins with no bars are
        # `continue`d above, so a held-but-blind position is never sold without
        # this (crypto no-sells bug fix). Evaluated = the symbols we scored.
        core.exit_blind_held(result, portfolio_emulator, data, list(result.keys()))
        if discovered:
            result["_nexus_discovered"] = discovered
        return core.apply_crypto_config(result, config, prices, portfolio_emulator)
