# INTELLISTOCK_SCHEMA: {"strategy": "Macd", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"fast": 12, "slow": 26, "signal": 9, "zero_line_filter": false, "histogram_expansion": false}}
# INTELLISTOCK_DESCRIPTION: MACD (12/26/9). Buy/sell on MACD vs signal line crossovers. Optional zero-line filter and histogram expansion.
# DIFFICULTY: 2
"""
MACD strategy. Strategy name from DB: "macd" (file macd.py, class Macd).
Uses Moving Average Convergence Divergence for momentum; signals buy/hold/sell
on MACD vs signal line crossovers and histogram strength.

Standard (12/26/9) is the industry default; professionals often add:
- Zero-line filter: only buy when MACD is at/below zero or crossing up from below (reduces false longs in overbought).
- Histogram expansion: require histogram to be expanding on crossover (confirms momentum).
- One-bar confirmation: wait one bar after crossover to avoid whipsaws (optional).

Returns: 1 = buy, 0 = hold, -1 = sell.
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
        intellistock_logger.log(msg, color, service="MacdStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[MacdStrategy] {msg}")


def _compute_macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """Compute MACD via TA-Lib. Returns (macd_line, signal_line, histogram) as 1d arrays."""
    if len(closes) < slow + signal:
        n = len(closes)
        return np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    return talib.MACD(closes.astype(np.float64), fastperiod=fast, slowperiod=slow, signalperiod=signal)


class Macd:
    """
    MACD momentum strategy. Uses MACD line vs signal line crossovers:
    - MACD crosses above signal -> buy (1)
    - MACD crosses below signal -> sell (-1)
    - Otherwise -> hold (0)

    Optional conditions (from DB/config):
    - use_histogram_threshold, histogram_threshold: filter weak signals by histogram vs price.
    - use_zero_line_filter: only buy when MACD at/below zero; only sell when at/above zero (reduces chasing).
    - use_histogram_expansion: require histogram expanding on crossover (confirms momentum).
    - confirm_one_bar: act one bar after crossover to reduce whipsaws.
    - follow_trend: if True, hold long when MACD > signal and short when MACD < signal (no cross needed).
    """

    def __init__(self):
        self.fast_period = 12
        self.slow_period = 26
        self.signal_period = 9
        self.min_bars = 35  # need at least slow + signal for first valid signal

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        """
        Run the MACD strategy.

        Args:
            symbol: str
            price: float or None (current price for symbol)
            current_time: datetime
            config: dict (strategy config from DB)
            conditions: dict (e.g. fast_period, slow_period, signal_period, use_histogram_threshold)
            data: dict symbol -> list of bars (each bar: t, o, h, l, c, v)

        Returns: 1 (buy), 0 (hold), -1 (sell)
        """
        if data is None:
            _log(f"[MACD] No data for {symbol}", "yellow")
            return (0, None, None, "No clear signal")
        if symbol not in data:
            _log(f"[MACD] Symbol {symbol} not in data", "yellow")
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        if not bars:
            _log(f"[MACD] {symbol}: no bars", "yellow")
            return (0, None, None, "No clear signal")
        # Extract close prices (bar['c'])
        closes = np.array([float(b.get("c") or 0) for b in bars if b.get("c") is not None], dtype=float)
        if len(closes) < self.min_bars:
            _log(f"[MACD] {symbol}: not enough bars ({len(closes)} < {self.min_bars})", "yellow")
            return (0, None, None, "No clear signal")
        # Optional overrides from conditions
        fast = int(conditions.get("fast_period", self.fast_period)) if conditions else self.fast_period
        slow = int(conditions.get("slow_period", self.slow_period)) if conditions else self.slow_period
        sig = int(conditions.get("signal_period", self.signal_period)) if conditions else self.signal_period
        fast = max(2, min(fast, 50))
        slow = max(fast + 1, min(slow, 100))
        sig = max(2, min(sig, 20))
        min_bars_need = slow + sig
        if len(closes) < min_bars_need:
            _log(f"[MACD] {symbol}: not enough bars for fast={fast} slow={slow} signal={sig}", "yellow")
            return (0, None, None, "No clear signal")
        macd_line, signal_line, histogram = _compute_macd(closes, fast=fast, slow=slow, signal=sig)

        # Last valid index
        valid = ~np.isnan(macd_line) & ~np.isnan(signal_line)
        if not np.any(valid):
            return (0, None, None, "No clear signal")
        last_idx = np.where(valid)[0][-1]
        if last_idx < 1:
            return (0, None, None, "No clear signal")
        macd_now = macd_line[last_idx]
        signal_now = signal_line[last_idx]
        macd_prev = macd_line[last_idx - 1]
        signal_prev = signal_line[last_idx - 1]
        hist_now = histogram[last_idx] if not np.isnan(histogram[last_idx]) else 0.0
        hist_prev = histogram[last_idx - 1] if last_idx >= 1 and not np.isnan(histogram[last_idx - 1]) else 0.0

        # One-bar confirmation: use previous bar's crossover so we don't act on the exact crossover bar (reduces whipsaws)
        confirm_idx = last_idx - 1 if (conditions.get("confirm_one_bar", False) if conditions else False) else last_idx
        if confirm_idx < 1:
            confirm_idx = last_idx
        macd_c = macd_line[confirm_idx]
        signal_c = signal_line[confirm_idx]
        macd_c_prev = macd_line[confirm_idx - 1]
        signal_c_prev = signal_line[confirm_idx - 1]
        cross_above = (macd_c_prev <= signal_c_prev) and (macd_c > signal_c)
        cross_below = (macd_c_prev >= signal_c_prev) and (macd_c < signal_c)

        # Optional zero-line filter (pro-style): only buy when MACD at or below zero / crossing up from below; only sell when at or above zero
        use_zero_filter = conditions.get("use_zero_line_filter", False) if conditions else False
        if use_zero_filter:
            if cross_above and macd_c > 0:
                cross_above = False  # ignore buy if MACD already above zero (avoid chasing)
            if cross_below and macd_c < 0:
                cross_below = False  # ignore sell if MACD already below zero

        # Optional histogram expansion: require histogram to be expanding on the signal bar (confirms momentum)
        use_hist_expansion = conditions.get("use_histogram_expansion", False) if conditions else False
        if use_hist_expansion:
            hist_c = histogram[confirm_idx] if not np.isnan(histogram[confirm_idx]) else 0.0
            hist_c_prev = histogram[confirm_idx - 1] if confirm_idx >= 1 and not np.isnan(histogram[confirm_idx - 1]) else 0.0
            if cross_above and hist_c <= hist_c_prev:
                cross_above = False  # buy only if histogram is expanding (rising)
            if cross_below and hist_c >= hist_c_prev:
                cross_below = False  # sell only if histogram is expanding (falling)

        # Optional histogram threshold to avoid weak signals (conditions: histogram_threshold as fraction of recent price)
        use_hist_threshold = conditions.get("use_histogram_threshold", False) if conditions else False
        hist_threshold = float(conditions.get("histogram_threshold", 0.0)) if conditions else 0.0
        if use_hist_threshold and hist_threshold != 0 and price and price > 0:
            abs_hist = abs(hist_now)
            min_hist = hist_threshold * price
            if abs_hist < min_hist:
                _log(f"[MACD] {symbol}: histogram {hist_now:.4f} below threshold ({min_hist:.4f}), hold", "cyan")
                return (0, None, None, "No clear signal")
        if cross_above:
            _log(f"[MACD] {symbol}: cross above (MACD={macd_c:.4f} > signal={signal_c:.4f}) -> buy", "green")
            return (1, None, None, f"MACD crossed above signal ({macd_c:.4f} > {signal_c:.4f})")
        if cross_below:
            _log(f"[MACD] {symbol}: cross below (MACD={macd_c:.4f} < signal={signal_c:.4f}) -> sell", "yellow")
            return (-1, None, None, f"MACD crossed below signal ({macd_c:.4f} < {signal_c:.4f})")

        # No crossover: optional trend follow (MACD above signal = bullish, below = bearish)
        follow_trend = conditions.get("follow_trend", False) if conditions else False
        if follow_trend:
            if macd_now > signal_now:
                return (1, None, None, "MACD above signal (trend follow)")
            if macd_now < signal_now:
                return (-1, None, None, "MACD below signal (trend follow)")
        return (0, None, None, "MACD no crossover")
