"""Symbol to sector to sector ETF, for the sector part of the split."""
from __future__ import annotations

import threading
from typing import Callable, Optional

#: yfinance sector names, normalised (lower case, spaces to underscores), to
#: the SPDR sector fund that tracks them.
SECTOR_ETFS = {
    "technology": "XLK",
    "financial_services": "XLF",
    "financial": "XLF",
    "healthcare": "XLV",
    "consumer_cyclical": "XLY",
    "consumer_defensive": "XLP",
    "energy": "XLE",
    "industrials": "XLI",
    "basic_materials": "XLB",
    "utilities": "XLU",
    "real_estate": "XLRE",
    "communication_services": "XLC",
}

#: Funds that track an index other than the S&P 500. Their "sector" series is
#: the plain index fund, so a Nasdaq-driven day in TQQQ is not read as
#: something specific to TQQQ.
FUND_BENCHMARKS = {
    "TQQQ": ("nasdaq_100", "QQQ"), "SQQQ": ("nasdaq_100", "QQQ"),
    "QLD": ("nasdaq_100", "QQQ"), "QQQM": ("nasdaq_100", "QQQ"),
    "SOXL": ("semiconductors", "SOXX"), "SOXS": ("semiconductors", "SOXX"),
    "SMH": ("semiconductors", "SOXX"),
    "TNA": ("small_caps", "IWM"), "TZA": ("small_caps", "IWM"),
    "GDX": ("gold", "GLD"), "GDXJ": ("gold", "GLD"), "NUGT": ("gold", "GLD"),
    "IAU": ("gold", "GLD"),
}

_CACHE: dict = {}
_LOCK = threading.Lock()


def _lookup(symbol: str) -> str:
    from swing_trader.sectors import get_symbol_sector

    return get_symbol_sector(symbol, allow_network=True)


def sector_for(symbol: str, *, lookup: Optional[Callable] = None) -> tuple:
    """``(sector, etf)``; both None for a fund, an index product or a symbol
    the provider does not classify."""
    if symbol in FUND_BENCHMARKS:
        return FUND_BENCHMARKS[symbol]
    if lookup is None:
        with _LOCK:
            if symbol in _CACHE:
                return _CACHE[symbol]
    try:
        sector = str((lookup or _lookup)(symbol) or "").strip().lower().replace(" ", "_")
    except Exception:
        sector = ""
    etf = SECTOR_ETFS.get(sector)
    result = (sector, etf) if etf and etf != symbol else (None, None)
    if lookup is None:
        with _LOCK:
            _CACHE[symbol] = result
    return result
