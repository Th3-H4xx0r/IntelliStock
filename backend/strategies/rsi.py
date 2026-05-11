# INTELLISTOCK_SCHEMA: {"strategy": "Rsi", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"period": 14, "oversold": 30, "overbought": 70, "use_midline": false}}
# INTELLISTOCK_DESCRIPTION: RSI (Wilder). Buy on oversold cross up, sell on overbought cross down. Optional 50 midline.
# DIFFICULTY: 2
"""
RSI strategy. Strategy name from DB: "rsi" or "RSI" (file rsi.py, class Rsi).
Uses Relative Strength Index (Wilder's smoothing, default 14-period).
- RSI < oversold (default 30) then crosses above -> buy
- RSI > overbought (default 70) then crosses below -> sell
Optional: use 50 midline (cross above 50 = buy, cross below 50 = sell).
Returns: 1 = buy, 0 = hold, -1 = sell.
"""

from __future__ import annotations

import numpy as np
import talib

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="RsiStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[RsiStrategy] {msg}")


def _compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI via TA-Lib (Wilder's smoothing). Returns array same length as closes; first values are NaN."""
    if period < 1 or len(closes) < period + 1:
        return np.full_like(closes, np.nan, dtype=float)
    return talib.RSI(closes.astype(np.float64), timeperiod=period)


class Rsi:
    """
    RSI momentum strategy. Classic overbought/oversold:
    - RSI crosses above oversold level (default 30) -> buy
    - RSI crosses below overbought level (default 70) -> sell

    Optional conditions:
    - period: RSI period (default 14)
    - overbought: level for sell signal (default 70)
    - oversold: level for buy signal (default 30)
    - use_midline: if True (default), also use 50 (cross above 50 = buy, cross below 50 = sell). Good for trending markets where RSI rarely hits 30/70.
    """

    def __init__(self):
        self.period = 14
        self.overbought = 70
        self.oversold = 30
        self.min_bars = 16  # period + 2 for at least one valid RSI and a prior

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        if data is None or symbol not in data:
            _log(f"[RSI] No data for {symbol}", "yellow")
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        if not bars:
            _log(f"[RSI] {symbol}: no bars", "yellow")
            return (0, None, None, "No clear signal")
        closes = np.array([float(b.get("c") or 0) for b in bars if b.get("c") is not None], dtype=float)
        period = int(conditions.get("period", self.period)) if conditions else self.period
        period = max(2, min(period, 50))
        overbought = float(conditions.get("overbought", self.overbought)) if conditions else self.overbought
        oversold = float(conditions.get("oversold", self.oversold)) if conditions else self.oversold
        # Default True so trending markets get signals (50-level crosses) when 30/70 are never hit
        use_midline = conditions.get("use_midline", True) if conditions else True
        min_bars_need = period + 2
        if len(closes) < min_bars_need:
            _log(f"[RSI] {symbol}: not enough bars ({len(closes)} < {min_bars_need})", "yellow")
            return (0, None, None, "No clear signal")
        rsi = _compute_rsi(closes, period)
        valid = ~np.isnan(rsi)
        if not np.any(valid):
            return (0, None, None, "No clear signal")
        last_idx = np.where(valid)[0][-1]
        if last_idx < 1:
            return (0, None, None, "No clear signal")
        rsi_now = rsi[last_idx]
        rsi_prev = rsi[last_idx - 1]

        # Cross above oversold -> buy
        cross_above_oversold = (rsi_prev <= oversold) and (rsi_now > oversold)
        # Cross below overbought -> sell
        cross_below_overbought = (rsi_prev >= overbought) and (rsi_now < overbought)
        # Optional: cross above 50 = buy, cross below 50 = sell
        cross_above_50 = (rsi_prev <= 50) and (rsi_now > 50)
        cross_below_50 = (rsi_prev >= 50) and (rsi_now < 50)

        if cross_above_oversold:
            _log(f"[RSI] {symbol}: cross above oversold ({oversold}) RSI={rsi_now:.1f} -> buy", "green")
            return (1, None, None, f"RSI crossed above oversold ({oversold}), now {rsi_now:.1f}")
        if cross_below_overbought:
            _log(f"[RSI] {symbol}: cross below overbought ({overbought}) RSI={rsi_now:.1f} -> sell", "yellow")
            return (-1, None, None, f"RSI crossed below overbought ({overbought}), now {rsi_now:.1f}")
        if use_midline:
            if cross_above_50:
                _log(f"[RSI] {symbol}: cross above 50 RSI={rsi_now:.1f} -> buy", "green")
                return (1, None, None, f"RSI crossed above 50, now {rsi_now:.1f}")
            if cross_below_50:
                _log(f"[RSI] {symbol}: cross below 50 RSI={rsi_now:.1f} -> sell", "yellow")
                return (-1, None, None, f"RSI crossed below 50, now {rsi_now:.1f}")
        return (0, None, None, f"RSI {rsi_now:.1f}, no signal")
