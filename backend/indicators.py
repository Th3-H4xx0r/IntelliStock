"""Shared TA-Lib indicator wrappers for IntelliStockV4 strategies.

Provides helper to convert bar-dict lists (keys: h/l/c/v or high/low/close/volume)
into numpy arrays, plus thin wrappers around TA-Lib functions.
"""
from __future__ import annotations

import numpy as np
import talib


def bars_to_arrays(bars: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Convert list of bar dicts to (high, low, close, volume) float64 arrays."""
    high = np.array([float(b.get("h") or b.get("high") or b.get("c") or b.get("close") or 0) for b in bars], dtype=np.float64)
    low = np.array([float(b.get("l") or b.get("low") or b.get("c") or b.get("close") or 0) for b in bars], dtype=np.float64)
    close = np.array([float(b.get("c") or b.get("close") or 0) for b in bars], dtype=np.float64)
    volume = np.array([float(b.get("v") or b.get("volume") or 0) for b in bars], dtype=np.float64)
    return high, low, close, volume
