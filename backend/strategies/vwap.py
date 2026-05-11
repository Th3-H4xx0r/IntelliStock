# INTELLISTOCK_SCHEMA: {"strategy": "Vwap", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"window": null, "band_pct": 0}}
# INTELLISTOCK_DESCRIPTION: VWAP crossover. Buy when close crosses above VWAP, sell when below. Optional rolling window and band filter.
# DIFFICULTY: 2
"""
VWAP strategy. Strategy name from DB: "vwap" or "VWAP" (file vwap.py, class Vwap).
Uses Volume Weighted Average Price. Typical price = (H+L+C)/3; VWAP = sum(TP*V)/sum(V) over window.
- Price (close) crosses above VWAP -> buy
- Price (close) crosses below VWAP -> sell
Optional: band filter (only signal when price is at least X% away from VWAP).
Returns: 1 = buy, 0 = hold, -1 = sell.
"""

from __future__ import annotations

import numpy as np

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="VwapStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[VwapStrategy] {msg}")


def _compute_vwap(bars: list, window: int | None = None) -> np.ndarray:
    """
    Compute VWAP for each bar. If window is None, use cumulative from start (anchored).
    Otherwise use rolling window of last `window` bars.
    Typical price = (H+L+C)/3; VWAP = sum(TP*V)/sum(V).
    Returns array of VWAP values; first (window-1) or 0 values may be nan if rolling.
    """
    n = len(bars)
    if n == 0:
        return np.array([])
    tp = np.zeros(n, dtype=float)
    vol = np.zeros(n, dtype=float)
    for i, b in enumerate(bars):
        h = float(b.get("h") or b.get("c") or 0)
        l = float(b.get("l") or b.get("c") or 0)
        c = float(b.get("c") or 0)
        v = float(b.get("v") or 1.0)
        if v <= 0:
            v = 1.0
        tp[i] = (h + l + c) / 3.0
        vol[i] = v
    if window is None or window >= n:
        # Anchored VWAP: cumulative
        cum_tpv = np.cumsum(tp * vol)
        cum_v = np.cumsum(vol)
        vwap = np.where(cum_v > 0, cum_tpv / cum_v, np.nan)
        return vwap
    # Rolling VWAP
    vwap = np.full(n, np.nan, dtype=float)
    for i in range(window - 1, n):
        tpv = np.sum(tp[i - window + 1 : i + 1] * vol[i - window + 1 : i + 1])
        v = np.sum(vol[i - window + 1 : i + 1])
        if v > 0:
            vwap[i] = tpv / v
    return vwap


class Vwap:
    """
    VWAP strategy. Price vs VWAP crossovers:
    - Close crosses above VWAP -> buy
    - Close crosses below VWAP -> sell

    Optional conditions:
    - window: number of bars for rolling VWAP (default 20). Use null/omit for anchored (cumulative) VWAP.
    - min_bars: minimum bars before first signal (default 2 for crossover)
    - band_pct: if set, only signal when price is at least this % away from VWAP (e.g. 0.001 = 0.1%)
    """

    def __init__(self):
        self.window = 20  # rolling by default so VWAP moves with price and produces more crossovers
        self.min_bars = 2

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        if data is None or symbol not in data:
            _log(f"[VWAP] No data for {symbol}", "yellow")
            return (0, None, None, "No clear signal")
        bars = data[symbol]
        if not bars:
            _log(f"[VWAP] {symbol}: no bars", "yellow")
            return (0, None, None, "No clear signal")
        # Default rolling window so we get crossovers in trending markets (anchored can stay one-sided)
        window = conditions.get("window") if conditions is not None else self.window
        if window is not None:
            try:
                window = int(window)
                window = max(2, min(window, 500))
            except (TypeError, ValueError):
                window = self.window
        min_bars_need = (window or 2) + 1
        if len(bars) < min_bars_need:
            _log(f"[VWAP] {symbol}: not enough bars ({len(bars)} < {min_bars_need})", "yellow")
            return (0, None, None, "No clear signal")
        vwap = _compute_vwap(bars, window)
        closes = np.array([float(b.get("c") or 0) for b in bars], dtype=float)
        valid = ~np.isnan(vwap)
        if not np.any(valid):
            return (0, None, None, "No clear signal")
        last_idx = np.where(valid)[0][-1]
        if last_idx < 1:
            return (0, None, None, "No clear signal")
        close_now = closes[last_idx]
        close_prev = closes[last_idx - 1]
        vwap_now = vwap[last_idx]
        vwap_prev = vwap[last_idx - 1]

        band_pct = float(conditions.get("band_pct", 0) or 0) if conditions else 0
        if band_pct < 0:
            band_pct = 0
        # Small tolerance (0.1%) so near-touches count as crosses; avoids missing due to float or flat VWAP
        tol = 0.001
        vp = vwap_prev if vwap_prev != 0 else 1e-9
        vn = vwap_now if vwap_now != 0 else 1e-9
        cross_above = (close_prev <= vp * (1 + tol)) and (close_now > vn * (1 - tol))
        cross_below = (close_prev >= vp * (1 - tol)) and (close_now < vn * (1 + tol))
        if band_pct > 0 and vwap_now != 0:
            # Require price to be at least band_pct away from VWAP (e.g. 0.002 = 0.2%)
            margin = abs(close_now - vwap_now) / abs(vwap_now)
            if margin < band_pct:
                cross_above = cross_below = False

        if cross_above:
            _log(f"[VWAP] {symbol}: close {close_now:.2f} cross above VWAP {vwap_now:.2f} -> buy", "green")
            return (1, None, None, "Buy signal generated")
        if cross_below:
            _log(f"[VWAP] {symbol}: close {close_now:.2f} cross below VWAP {vwap_now:.2f} -> sell", "yellow")
            return (-1, None, None, "Sell signal generated")
        return (0, None, None, "No clear signal")