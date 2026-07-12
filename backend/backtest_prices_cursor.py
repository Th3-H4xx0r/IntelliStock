"""Monotonic-cursor cache for the backtest per-step price lookup.

``_get_prices_at_time`` (broker.py) previously re-scanned every symbol's full
bar history from ``bars[0]`` on EVERY backtest step to find the latest bar at
or before ``current_time`` — O(bars-so-far) per step, i.e. O(nÂ²) over the run,
the dominant cost of long/high-cadence crypto backtests.

Backtest time advances strictly forward, so we keep a per-symbol cursor and
advance it only by the newly-eligible bars each call — O(new_bars)/call. This
mirrors ``backtest_price_history`` (the sibling that slices the full history);
this module returns just the latest CLOSE per symbol.

Only used for the NON-daily main-loop lookup (crypto/intraday). The daily-bar
path and all non-monotonic / one-off call sites keep the original full scan, so
this never has to reason about the daily same-day-bar rule or a different
``data`` object. Self-heals: cursors reset when ``current_utc`` rewinds, and the
per-symbol filtered view extends as bars are appended (pagination/discovery).

Time conversion is injected by the caller (broker.py defines it locally),
keeping this module decoupled and import-safe.
"""

from __future__ import annotations

from typing import Any, Callable


CACHE: dict[str, Any] = {
    "cursors": {},            # symbol -> int (bars consumed: bars[:idx] all <= current)
    "last_current_utc": None,  # for rewind detection
    "filtered": {},           # symbol -> {"bars": [well-formed bars], "src_len": int}
}


def invalidate() -> None:
    """Drop the cursor cache. Idempotent."""
    CACHE["cursors"] = {}
    CACHE["last_current_utc"] = None
    CACHE["filtered"] = {}


def latest_price_at(
    data: dict,
    symbols,
    current_utc,
    *,
    bar_time_to_datetime: Callable,
) -> dict:
    """Return ``{symbol: float(close)}`` for the latest bar with ``t <=
    current_utc`` (non-daily). Symbols with no eligible bar are omitted.

    ``current_utc`` must be a naive-UTC datetime (as produced by broker's
    ``_current_time_to_utc``). ``bar_time_to_datetime`` converts a bar's ``t``
    to naive UTC or None for malformed bars.
    """
    out: dict = {}
    last = CACHE.get("last_current_utc")
    if last is not None and current_utc < last:
        CACHE["cursors"] = {}          # rewind -> re-advance from the start
    CACHE["last_current_utc"] = current_utc

    cursors: dict = CACHE["cursors"]
    filtered: dict = CACHE["filtered"]
    for sym in (symbols or []):
        bars = data.get(sym) or []
        n = len(bars)
        f = filtered.get(sym)
        if f is None:
            f = {"bars": [], "src_len": 0}
            filtered[sym] = f
        if f["src_len"] < n:
            # Extend the malformed-stripped view with newly-appended bars.
            for j in range(f["src_len"], n):
                if bar_time_to_datetime(bars[j].get("t")) is not None:
                    f["bars"].append(bars[j])
            f["src_len"] = n
        elif f["src_len"] > n:
            # Source shrank/replaced-shorter: rebuild and reset this cursor.
            f["bars"] = [b for b in bars if bar_time_to_datetime(b.get("t")) is not None]
            f["src_len"] = n
            cursors[sym] = 0

        clean = f["bars"]
        nf = len(clean)
        idx = cursors.get(sym, 0)
        if idx > nf:
            idx = 0
        while idx < nf and bar_time_to_datetime(clean[idx].get("t")) <= current_utc:
            idx += 1
        cursors[sym] = idx
        if idx > 0:
            bar = clean[idx - 1]
            if "c" in bar:
                try:
                    out[sym] = float(bar["c"])
                except (TypeError, ValueError):
                    pass
    return out
