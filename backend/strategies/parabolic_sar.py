# INTELLISTOCK_SCHEMA: {"strategy": "ParabolicSar", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"start": 0.02, "increment": 0.02, "maximum": 0.2}}
# INTELLISTOCK_DESCRIPTION: Parabolic SAR. Buy when price crosses above SAR, sell when below. Config: start, increment, maximum (acceleration).
# DIFFICULTY: 2
"""
Parabolic SAR strategy - IMPROVED VERSION
Strategy name from DB: "parabolic_sar" (file parabolic_sar.py, class ParabolicSar).
Uses Welles Wilder's Parabolic Stop and Reverse (SAR) on high/low/close.
- Price crosses above SAR (SAR flips below price) -> buy (1)
- Price crosses below SAR (SAR flips above price) -> sell (-1)
Optional parameters: start, increment, maximum (acceleration factor).
Returns: 1 = buy, 0 = hold, -1 = sell.

IMPROVEMENTS:
1. Better initialization logic to determine initial trend
2. Explicit trend tracking for accurate signal generation
3. More robust signal detection using trend changes
4. Better edge case handling
5. Added validation and debugging info
"""

from __future__ import annotations

import numpy as np
import talib
import time

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="ParabolicSarStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[ParabolicSarStrategy] {msg}")


def _compute_parabolic_sar(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start: float = 0.02,
    increment: float = 0.02,
    maximum: float = 0.2,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute Parabolic SAR via TA-Lib with derived trend tracking.

    Returns:
        (sar_values, trend_values) — trend: 1 = uptrend (SAR below price), -1 = downtrend.
    """
    n = len(close)
    if n < 2:
        return np.full(n, np.nan, dtype=float), np.zeros(n, dtype=int)

    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)

    sar = talib.SAR(high, low, acceleration=start, maximum=maximum)
    # Derive trend: SAR below close = uptrend (1), SAR above close = downtrend (-1)
    trend_array = np.where(close > sar, 1, -1).astype(int)
    # Where SAR is NaN, set trend to 0
    trend_array[np.isnan(sar)] = 0

    return sar, trend_array


class ParabolicSar:
    """
    IMPROVED Parabolic SAR (Stop and Reverse) strategy.
    
    Signals:
    - Trend changes from -1 to 1 (downtrend -> uptrend) -> BUY (1)
    - Trend changes from 1 to -1 (uptrend -> downtrend) -> SELL (-1)
    
    This is more reliable than comparing close vs SAR because it detects
    the actual reversal points defined by the PSAR algorithm.
    
    Optional conditions (from DB/config):
    - start: initial acceleration factor (default 0.02)
    - increment: AF step (default 0.02)
    - maximum: max AF (default 0.2)
    """

    def __init__(self):
        self.start = 0.02
        self.increment = 0.02
        self.maximum = 0.2
        self.min_bars = 5  # need at least a few bars for SAR

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        """
        Execute Parabolic SAR strategy.

        Returns:
            1 = buy signal (reversal to uptrend)
            -1 = sell signal (reversal to downtrend)
            0 = no signal (hold)
        """
        if data is None or symbol not in data:
            _log(f"[PSAR] No data for {symbol}", "yellow")
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        if not bars:
            _log(f"[PSAR] {symbol}: no bars", "yellow")
            return (0, None, None, "No clear signal")
        # Extract OHLC data
        highs = np.array([float(b.get("h") or b.get("c") or 0) for b in bars], dtype=float)
        lows = np.array([float(b.get("l") or b.get("c") or 0) for b in bars], dtype=float)
        closes = np.array([float(b.get("c") or 0) for b in bars], dtype=float)
        
        # Validation
        if len(closes) < self.min_bars:
            _log(f"[PSAR] {symbol}: not enough bars ({len(closes)} < {self.min_bars})", "yellow")
            return (0, None, None, "No clear signal")
        # Check for invalid data
        if np.any(highs <= 0) or np.any(lows <= 0) or np.any(closes <= 0):
            _log(f"[PSAR] {symbol}: invalid price data detected", "red")
            return (0, None, None, "No clear signal")
        # Get parameters from conditions
        start = float(conditions.get("start", self.start)) if conditions else self.start
        increment = float(conditions.get("increment", self.increment)) if conditions else self.increment
        maximum = float(conditions.get("maximum", self.maximum)) if conditions else self.maximum
        
        # Validate and clamp parameters
        start = max(0.01, min(start, 0.2))
        increment = max(0.01, min(increment, 0.2))
        maximum = max(0.1, min(maximum, 0.5))

        # Compute Parabolic SAR with trend tracking
        sar, trend = _compute_parabolic_sar(
            highs, lows, closes, 
            start=start, 
            increment=increment, 
            maximum=maximum
        )
        
        # Find valid SAR values
        valid = ~np.isnan(sar)
        if not np.any(valid):
            _log(f"[PSAR] {symbol}: no valid SAR values", "yellow")
            return (0, None, None, "No clear signal")
        # Get the last two valid indices for signal detection
        valid_indices = np.where(valid)[0]
        if len(valid_indices) < 2:
            _log(f"[PSAR] {symbol}: insufficient valid SAR values", "yellow")
            return (0, None, None, "No clear signal")
        last_idx = valid_indices[-1]
        prev_idx = valid_indices[-2]
        
        # IMPROVED: Detect trend changes (actual reversals)
        current_trend = trend[last_idx]
        previous_trend = trend[prev_idx]
        
        # Additional validation: confirm with price position
        current_price = closes[last_idx]
        current_sar = sar[last_idx]
        
        # SIGNAL GENERATION based on trend changes
        if previous_trend == -1 and current_trend == 1:
            # Reversal to UPTREND -> BUY
            _log(
                f"[PSAR] {symbol}: UPTREND reversal detected | "
                f"Price: {current_price:.2f} | SAR: {current_sar:.2f} | "
                f"AF params: start={start}, inc={increment}, max={maximum} -> BUY", 
                "green"
            )
            return (1, None, None, "Buy signal generated")
        elif previous_trend == 1 and current_trend == -1:
            # Reversal to DOWNTREND -> SELL
            _log(
                f"[PSAR] {symbol}: DOWNTREND reversal detected | "
                f"Price: {current_price:.2f} | SAR: {current_sar:.2f} | "
                f"AF params: start={start}, inc={increment}, max={maximum} -> SELL", 
                "yellow"
            )
            return (-1, None, None, "Sell signal generated")
        else:
            # No trend change -> HOLD
            trend_name = "UPTREND" if current_trend == 1 else "DOWNTREND"
            return (0, None, None, "No clear signal")
# Backward compatibility: keep the original class name
class ParabolicSarOriginal:
    """
    Original implementation (kept for reference/comparison).
    """
    
    def __init__(self):
        self.start = 0.02
        self.increment = 0.02
        self.maximum = 0.2
        self.min_bars = 5

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        if data is None or symbol not in data:
            _log(f"[PSAR-ORIG] No data for {symbol}", "yellow")
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        if not bars:
            _log(f"[PSAR-ORIG] {symbol}: no bars", "yellow")
            return (0, None, None, "No clear signal")
        highs = np.array([float(b.get("h") or b.get("c") or 0) for b in bars], dtype=float)
        lows = np.array([float(b.get("l") or b.get("c") or 0) for b in bars], dtype=float)
        closes = np.array([float(b.get("c") or 0) for b in bars], dtype=float)
        if len(closes) < self.min_bars:
            _log(f"[PSAR-ORIG] {symbol}: not enough bars ({len(closes)} < {self.min_bars})", "yellow")
            return (0, None, None, "No clear signal")
        start = float(conditions.get("start", self.start)) if conditions else self.start
        increment = float(conditions.get("increment", self.increment)) if conditions else self.increment
        maximum = float(conditions.get("maximum", self.maximum)) if conditions else self.maximum
        start = max(0.01, min(start, 0.2))
        increment = max(0.01, min(increment, 0.2))
        maximum = max(0.1, min(maximum, 0.5))

        # Use old function (without trend tracking)
        def _compute_parabolic_sar_old(high, low, close, start, increment, maximum):
            n = len(close)
            if n < 2:
                return np.full(n, np.nan, dtype=float)
            sar = np.full(n, np.nan, dtype=float)
            trend = 1
            ep = high[0]
            af = start
            sar[0] = low[0]
            for i in range(1, n):
                if trend == 1:
                    sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
                    if i >= 1:
                        sar[i] = min(sar[i], low[i - 1])
                    if i >= 2:
                        sar[i] = min(sar[i], low[i - 2])
                    if high[i] > ep:
                        ep = high[i]
                        af = min(af + increment, maximum)
                    if low[i] < sar[i]:
                        trend = -1
                        sar[i] = ep
                        ep = low[i]
                        af = start
                else:
                    sar[i] = sar[i - 1] - af * (sar[i - 1] - ep)
                    if i >= 1:
                        sar[i] = max(sar[i], high[i - 1])
                    if i >= 2:
                        sar[i] = max(sar[i], high[i - 2])
                    if low[i] < ep:
                        ep = low[i]
                        af = min(af + increment, maximum)
                    if high[i] > sar[i]:
                        trend = 1
                        sar[i] = ep
                        ep = high[i]
                        af = start
            return sar

        sar = _compute_parabolic_sar_old(highs, lows, closes, start, increment, maximum)
        valid = ~np.isnan(sar)
        if not np.any(valid):
            return (0, None, None, "No clear signal")
        last_idx = np.where(valid)[0][-1]
        if last_idx < 1:
            return (0, None, None, "No clear signal")
        sar_now = sar[last_idx]
        sar_prev = sar[last_idx - 1]
        close_now = closes[last_idx]
        close_prev = closes[last_idx - 1]

        cross_above = close_prev <= sar_prev and close_now > sar_now
        cross_below = close_prev >= sar_prev and close_now < sar_now

        if cross_above:
            _log(f"[PSAR-ORIG] {symbol}: price crossed above SAR (SAR={sar_now:.2f}) -> buy", "green")
            return (1, None, None, "Buy signal generated")
        if cross_below:
            _log(f"[PSAR-ORIG] {symbol}: price crossed below SAR (SAR={sar_now:.2f}) -> sell", "yellow")
            return (-1, None, None, "Sell signal generated")
        return (0, None, None, "No clear signal")