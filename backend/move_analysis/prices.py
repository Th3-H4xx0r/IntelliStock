"""Daily prices for the move analysis: a symbol, SPY and a sector ETF, aligned
on the symbol's trading days.

Returns are taken from adjusted closes so a dividend or split is not read as
a move; the unadjusted close is what the reader sees.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

MARKET_SYMBOL = "SPY"
NY = ZoneInfo("America/New_York")

_CACHE: dict = {}
_CACHE_TTL_SEC = 300.0
_CACHE_LOCK = threading.Lock()


class NoPriceHistory(Exception):
    """The provider returned no daily bars for the symbol."""


@dataclass(frozen=True)
class Aligned:
    dates: list
    closes: list
    basis: list
    market: list
    sector: Optional[list]
    volumes: list


def session_is_open(now: Optional[datetime] = None) -> bool:
    """True while today's daily bar is still forming (before 16:05 New York
    on a weekday). Holidays are not known here; on a holiday there is simply
    no bar for today, so nothing is dropped."""
    local = (now or datetime.now(tz=NY)).astimezone(NY)
    if local.weekday() >= 5:
        return False
    return (local.hour, local.minute) < (16, 5)


def _yfinance_daily(symbol: str, start: date) -> list:
    import yfinance as yf

    history = yf.Ticker(symbol).history(
        start=start.isoformat(), interval="1d", auto_adjust=False)
    rows = []
    if history is None or history.empty or "Close" not in history:
        return rows
    has_adj = "Adj Close" in history
    for stamp, row in history.iterrows():
        close = row.get("Close")
        if close is None or close != close or close <= 0:
            continue
        adj = row.get("Adj Close") if has_adj else close
        if adj is None or adj != adj or adj <= 0:
            adj = close
        volume = row.get("Volume")
        if volume is None or volume != volume:
            volume = None
        day = stamp.date() if hasattr(stamp, "date") else stamp
        rows.append({"date": day.isoformat(), "close": round(float(close), 4),
                     "adj": float(adj),
                     "volume": None if volume is None else float(volume)})
    return rows


def fetch_daily(symbol: str, start: date, *, provider: Optional[Callable] = None,
                now: Optional[datetime] = None) -> list:
    """Finished daily bars for ``symbol`` from ``start``, oldest first."""
    key = (symbol, start.isoformat())
    stamp = time.time()
    if provider is None:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit and stamp - hit[0] < _CACHE_TTL_SEC:
            return list(hit[1])
    rows = (provider or _yfinance_daily)(symbol, start)
    local_now = (now or datetime.now(tz=NY)).astimezone(NY)
    if rows and session_is_open(local_now) and rows[-1]["date"] == local_now.date().isoformat():
        rows = rows[:-1]
    if provider is None:
        with _CACHE_LOCK:
            for old in [k for k, v in _CACHE.items() if stamp - v[0] >= _CACHE_TTL_SEC]:
                del _CACHE[old]
            _CACHE[key] = (stamp, list(rows))
    return rows


def load_aligned(symbol: str, sector_etf: Optional[str], *, days: int,
                 lookback: int, provider: Optional[Callable] = None,
                 now: Optional[datetime] = None) -> Aligned:
    """Bars covering ``days`` calendar days plus enough history before them
    to estimate the betas."""
    local_now = (now or datetime.now(tz=NY)).astimezone(NY)
    # lookback is in trading days; 1.6x plus a margin covers weekends/holidays.
    start = local_now.date() - timedelta(days=days + int(lookback * 1.6) + 14)

    own = fetch_daily(symbol, start, provider=provider, now=local_now)
    if not own:
        raise NoPriceHistory(symbol)
    market_rows = (own if symbol == MARKET_SYMBOL
                   else fetch_daily(MARKET_SYMBOL, start, provider=provider, now=local_now))
    market = {row["date"]: row["adj"] for row in market_rows}
    sector = None
    if sector_etf and sector_etf != symbol:
        try:
            sector_rows = fetch_daily(sector_etf, start, provider=provider, now=local_now)
        except Exception:
            sector_rows = []
        if sector_rows:
            sector = {row["date"]: row["adj"] for row in sector_rows}

    dates = [row["date"] for row in own]
    return Aligned(
        dates=dates,
        closes=[row["close"] for row in own],
        basis=[row["adj"] for row in own],
        market=[market.get(d) for d in dates],
        sector=None if sector is None else [sector.get(d) for d in dates],
        volumes=[row["volume"] for row in own],
    )
