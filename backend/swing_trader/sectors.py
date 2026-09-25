"""Sectors, ported from ST paper_trader.py:92-115.

ST resolved every symbol through yfinance on first use and cached it for the
process. Here the stored SwingSectorMap (scripts/build_swing_reference_data.py:
yfinance info.sector + ST's overrides, normalised the way ST normalised) is
read first, and yfinance is only a LIVE fallback for a symbol the map lacks
(spec §9 item 15). The map is static and labelled not point-in-time.
"""
from __future__ import annotations

import yfinance as yf

from swing_trader.universe import norm_symbol

# Sector map for correlation check — covers S&P 500 major sectors
# Symbol → sector string. Unknown symbols default to "unknown" (allowed through)
_SECTOR_OVERRIDES: dict[str, str] = {
    # ETFs and commodities not in S&P 500
    "SPY": "broad_market", "QQQ": "broad_market",
    "GLD": "commodity",    "XLE": "energy",
    "IWM": "broad_market", "DIA": "broad_market",
}


def normalize_sector(raw) -> str:
    """ST's normalisation (paper_trader.py:111): lower case, spaces to _."""
    return str(raw if raw else "unknown").lower().replace(" ", "_")


def get_symbol_sector(symbol, sector_map=None, *, allow_network=False,
                      cache=None) -> str:
    """Return the sector for a symbol: override, stored map, then (live only)
    yfinance, cached in `cache`."""
    if symbol in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[symbol]
    key = norm_symbol(symbol)
    if sector_map and key in sector_map:
        return sector_map[key]
    if cache is not None and key in cache:
        return cache[key]
    if not allow_network:
        return "unknown"
    try:
        info   = yf.Ticker(symbol).info
        sector = info.get("sector", "unknown").lower().replace(" ", "_")
        if cache is not None:
            cache[key] = sector
        return sector
    except Exception:
        return "unknown"
