# INTELLISTOCK_SCHEMA: {"strategy": "Allocator", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "low", "long_ma_period": 20, "deploy_fraction": 0.95, "vol_lookback": 20}}
# INTELLISTOCK_DESCRIPTION: Band 3 allocator crypto strategy. Broad majors on daily/weekly bars. Holds coins trading above their long-term MA with inverse-volatility sizing hints; goes fully to USDC (risk-off) whenever BTC is below its long-term MA.
# DIFFICULTY: 3
"""
Allocator crypto strategy (run_once). DB name: "allocator" (file allocator.py,
class Allocator).

Slow, broad, regime-aware allocation:
- Regime gate: if BTC is below its long-term MA -> full risk-off (exit all).
- Otherwise hold every coin trading above its own long-term MA -> buy (1);
  coins below the MA -> exit (-1) if held, else hold (0).
When the seed universe is empty the majors are auto-discovered (ranked tradable
universe), falling back to ``DEFAULT_MAJORS`` on any error. Returns 1 = buy,
0 = hold, -1 = sell, plus ``_nexus_discovered`` for auto-picked pairs.
Per-symbol sizing is left to the broker's default sizing.
"""

from __future__ import annotations

from typing import List, Mapping, Optional

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

DEFAULT_MAJORS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD",
    "LTC/USD", "DOT/USD", "BCH/USD", "UNI/USD", "AAVE/USD",
]

BTC = "BTC/USD"

# Bars used to RANK the discovered universe (trading bars arrive from the
# broker's ``data`` on the next tick once discovery expands the symbol set).
_DISCOVERY_TIMEFRAME = "1Day"

# Shared helpers (single source of truth in core).
_series = core.series
_held_symbols = core.held_symbols


def _above_ma(bars, period):
    """True/False whether the last close is above its ``period`` SMA, or None.

    Returns None when there are too few bars to compute a valid SMA.
    """
    closes = _series(bars, "c")
    if len(closes) < period + 2:
        return None
    sma = talib.SMA(closes, timeperiod=period)
    ma_last = sma[-1]
    if np.isnan(ma_last):
        return None
    return bool(closes[-1] > ma_last)


class Allocator:
    """Regime-gated, inverse-vol broad crypto allocator."""

    def __init__(self):
        self.long_ma_period = 20
        self.deploy_fraction = 0.95
        self.vol_lookback = 20

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

        period = max(2, int(settings.get("long_ma_period", self.long_ma_period)))

        data = data or {}
        seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        if seed:
            candidates = seed
        else:
            # No seed list -> auto-discover the best coins, majors as a fallback.
            band = str(settings.get("band", "low"))
            disc_k = max(1, int(settings.get("discovery_k", 10)))
            timeframe = str(settings.get("discovery_timeframe", _DISCOVERY_TIMEFRAME))
            candidates = core.discover_universe(band, disc_k, settings, timeframe) or DEFAULT_MAJORS

        seed_set = set(seed)
        # Surface auto-picked pairs so the broker expands the universe and fetches
        # their bars — even before ``data`` holds them (first discovery tick).
        discovered = [s for s in candidates if s not in seed_set]
        universe = [s for s in candidates if data.get(s)]

        result: dict = {}
        if discovered:
            result["_nexus_discovered"] = discovered
        if not universe:
            return result

        held = _held_symbols(portfolio_emulator, universe)

        # Regime gate: BTC below its long-term MA => full risk-off.
        btc_above = _above_ma(data.get(BTC), period) if data.get(BTC) else None
        if btc_above is False:
            for sym in universe:
                result[sym] = -1 if sym in held else 0
            return result

        # Per-symbol regime: hold coins above their long-term MA, exit the rest.
        for sym in universe:
            above = _above_ma(data.get(sym), period)
            if above is None:
                result[sym] = -1 if sym in held else 0
            elif above:
                result[sym] = 1
            else:
                result[sym] = -1 if sym in held else 0
        return result
