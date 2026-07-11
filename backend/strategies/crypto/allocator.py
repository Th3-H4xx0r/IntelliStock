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
- Sizing hints are inverse-volatility weights across the held set, normalised to
  ``deploy_fraction`` (<= 1), returned in ``_nexus_position_sizes``.
Returns 1 = buy, 0 = hold, -1 = sell, plus ``_nexus_discovered``.
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
# Volatility floor so a perfectly-flat series can't produce an infinite
# inverse-vol weight.
_VOL_FLOOR = 1e-4


def _series(bars, key: str) -> np.ndarray:
    return np.array([float(b.get(key) or 0) for b in (bars or [])], dtype=float)


def _held_symbols(portfolio_emulator, universe):
    held = set()
    if portfolio_emulator is None:
        return held
    try:
        positions = portfolio_emulator.get_positions() or {}
    except Exception:
        return held
    for sym in universe:
        if float(positions.get(sym, 0) or 0) > 0:
            held.add(sym)
    return held


def _above_ma_and_vol(bars, period, vol_lookback):
    """(above_ma, volatility) for one symbol, or None when too few bars."""
    closes = _series(bars, "c")
    if len(closes) < period + 2:
        return None
    sma = talib.SMA(closes, timeperiod=period)
    ma_last = sma[-1]
    if np.isnan(ma_last):
        return None
    above = closes[-1] > ma_last
    rets = np.diff(closes) / np.where(closes[:-1] == 0, np.nan, closes[:-1])
    vol = float(np.nanstd(rets[-vol_lookback:])) if rets.size else 0.0
    return above, max(vol, _VOL_FLOOR)


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
        deploy = max(0.0, min(float(settings.get("deploy_fraction", self.deploy_fraction)), 1.0))
        vol_lookback = max(2, int(settings.get("vol_lookback", self.vol_lookback)))

        data = data or {}
        seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        candidates = seed if seed else DEFAULT_MAJORS
        universe = [s for s in candidates if data.get(s)]
        seed_set = set(seed)
        discovered = [s for s in universe if s not in seed_set]

        result: dict = {}
        if discovered:
            result["_nexus_discovered"] = discovered
        if not universe:
            return result

        held = _held_symbols(portfolio_emulator, universe)

        # Regime gate: BTC below its long-term MA => full risk-off.
        btc_metrics = _above_ma_and_vol(data.get(BTC), period, vol_lookback) if data.get(BTC) else None
        if btc_metrics is not None and not btc_metrics[0]:
            for sym in universe:
                result[sym] = -1 if sym in held else 0
            return result

        # Per-symbol regime + volatility.
        above_set = []
        vols = {}
        for sym in universe:
            m = _above_ma_and_vol(data.get(sym), period, vol_lookback)
            if m is None:
                result[sym] = -1 if sym in held else 0
                continue
            above, vol = m
            if above:
                above_set.append(sym)
                vols[sym] = vol
                result[sym] = 1
            else:
                result[sym] = -1 if sym in held else 0

        # Inverse-vol sizing across the held set, normalised to deploy fraction.
        if above_set:
            inv = {s: 1.0 / vols[s] for s in above_set}
            total_inv = sum(inv.values())
            if total_inv > 0:
                sizes = {s: deploy * inv[s] / total_inv for s in above_set}
                result["_nexus_position_sizes"] = sizes
        return result
