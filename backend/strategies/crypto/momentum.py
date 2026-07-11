# INTELLISTOCK_SCHEMA: {"strategy": "Momentum", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "medium", "fast_ema": 10, "slow_ema": 30, "momentum_lookback": 20, "top_k": 3, "adx_period": 14, "adx_min": 20, "risk_off_breadth": 0.5, "target_vol": 0.02, "max_frac": 0.25}}
# INTELLISTOCK_DESCRIPTION: Band 2 momentum crypto strategy. Trend-follows ~5-8 majors (from the seed list or discovery): EMA fast/slow cross with an ADX chop filter, holds the top-K by momentum, vol-targets sizing, and goes all-to-USD when the basket is in an aggregate downtrend.
# DIFFICULTY: 3
"""
Momentum crypto strategy (run_once). DB name: "momentum" (file momentum.py,
class Momentum).

Trend-following across a handful of majors:
- Uptrend = fast-EMA > slow-EMA and a non-chop ADX reading.
- Rank the universe by lookback momentum; the top-K uptrending pairs -> buy (1).
- Non-top / non-trending pairs -> exit (-1) if held, else hold (0).
- Aggregate downtrend (breadth of uptrending names below ``risk_off_breadth``)
  -> risk-off: exit everything (held -> -1, others 0).
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
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD",
    "LINK/USD", "LTC/USD", "DOT/USD", "BCH/USD",
]

# Bars used to RANK the discovered universe (the trading bars come from the
# broker's ``data`` on the next tick once discovery expands the symbol set).
_DISCOVERY_TIMEFRAME = "1Hour"

# Shared helpers (single source of truth in core).
_series = core.series
_held_symbols = core.held_symbols


class Momentum:
    """Trend-following top-K momentum on crypto majors."""

    def __init__(self):
        self.fast_ema = 10
        self.slow_ema = 30
        self.momentum_lookback = 20
        self.top_k = 3
        self.adx_period = 14
        self.adx_min = 20
        self.risk_off_breadth = 0.5
        self.target_vol = 0.02
        self.max_frac = 0.25

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

        fast_p = max(2, int(settings.get("fast_ema", self.fast_ema)))
        slow_p = max(fast_p + 1, int(settings.get("slow_ema", self.slow_ema)))
        lookback = max(2, int(settings.get("momentum_lookback", self.momentum_lookback)))
        top_k = max(1, int(settings.get("top_k", self.top_k)))
        adx_period = max(2, int(settings.get("adx_period", self.adx_period)))
        adx_min = float(settings.get("adx_min", self.adx_min))
        risk_off_breadth = float(settings.get("risk_off_breadth", self.risk_off_breadth))
        min_bars = max(slow_p, adx_period * 2, lookback) + 2

        data = data or {}
        seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        if seed:
            candidates = seed
        else:
            # No seed list -> auto-discover the best coins, majors as a fallback.
            band = str(settings.get("band", "medium"))
            disc_k = max(top_k, int(settings.get("discovery_k", 10)))
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
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        held = _held_symbols(portfolio_emulator, universe)

        # Per-symbol trend metrics.
        metrics = {}
        for sym in universe:
            bars = data.get(sym) or []
            closes = _series(bars, "c")
            highs = _series(bars, "h")
            lows = _series(bars, "l")
            if len(closes) < min_bars:
                metrics[sym] = None
                continue
            fast_ema = talib.EMA(closes, timeperiod=fast_p)
            slow_ema = talib.EMA(closes, timeperiod=slow_p)
            fe, se = fast_ema[-1], slow_ema[-1]
            ref = closes[-1 - lookback] if len(closes) > lookback else closes[0]
            mom = (closes[-1] / ref - 1.0) if ref > 0 else 0.0
            try:
                adx = talib.ADX(highs, lows, closes, timeperiod=adx_period)
                adx_last = adx[-1]
            except Exception:
                adx_last = np.nan
            chop = (not np.isnan(adx_last)) and (adx_last < adx_min)
            trend_up = (not np.isnan(fe)) and (not np.isnan(se)) and (fe > se) and (not chop)
            metrics[sym] = {"mom": mom, "trend_up": trend_up}

        valid = {s: m for s, m in metrics.items() if m is not None}
        if not valid:
            for sym in universe:
                result[sym] = -1 if sym in held else 0
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        # Aggregate-downtrend risk-off.
        uptrend = [s for s, m in valid.items() if m["trend_up"] and m["mom"] > 0]
        breadth = len(uptrend) / len(valid)
        if breadth < risk_off_breadth:
            for sym in universe:
                result[sym] = -1 if sym in held else 0
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        # Rank uptrending names by momentum; hold the top-K.
        ranked = sorted(uptrend, key=lambda s: (-valid[s]["mom"], s))
        winners = set(ranked[:top_k])

        for sym in universe:
            if sym in winners:
                result[sym] = 1
            else:
                result[sym] = -1 if sym in held else 0
        return core.apply_crypto_config(result, config, prices, portfolio_emulator)
