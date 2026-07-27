"""Monotonic-cursor cache for backtest price-history slicing.

Backtest time advances strictly forward, so the price-history slice
available to the strategy can be computed in O(new_bars) per call
rather than O(total_bars). The previous implementation rebuilt every
symbol's slice from scratch on every backtest bar — for a 60-symbol ×
5000-bar/symbol backtest at 1200s granularity, that's ~300k iterations
per bar × ~3000 bars = ~900M pure dict-lookup ops, easily the dominant
cost of the backtest loop.

This module owns the cursor state so it can be tested without importing
broker.py (which is not import-safe — it runs argparse + the backtest
main path at module load).

Public API:
- ``get_price_history_up_to_current(data, symbols, current_time, *,
  daily_mode, bar_time_to_datetime, current_time_to_utc)`` — returns
  dict[symbol] -> list[bar] containing only bars at or before
  ``current_time``.
- ``invalidate_cursor()`` — drop the cache. Used after operations
  that mutate ``data`` in a way that would break the cursor invariant.
- ``CACHE`` — the module-level state dict (exposed for inspection /
  test reset; do not mutate from production code).

Time-conversion helpers are injected by the caller because broker.py
defines those locally; injecting keeps this module decoupled.
"""

from __future__ import annotations

from typing import Any, Callable


CACHE: dict[str, Any] = {
    "cursors": {},          # symbol -> int (next-bar index after last call)
    "last_current_utc": None,  # for rewind detection
    "filtered_bars": {},    # symbol -> list[bar] (well-formed only, slice base)
}


def invalidate_cursor() -> None:
    """Drop the cursor cache. Idempotent."""
    CACHE["cursors"] = {}
    CACHE["last_current_utc"] = None
    CACHE["filtered_bars"] = {}


def get_price_history_up_to_current(
    data: dict,
    symbols,
    current_time,
    *,
    bar_time_to_datetime: Callable,
    current_time_to_utc: Callable,
    bar_available_at: Callable,
) -> dict:
    """Return bars whose completed values are available to the simulation.

    Uses a per-symbol monotonic cursor to avoid re-scanning bars that
    have already been included in a prior call. Cursor resets if
    ``current_time`` rewinds.

    Args:
        data: dict mapping symbol -> list of bar dicts. Bars must be
            sorted by ``t`` ascending.
        symbols: iterable of symbols to slice.
        current_time: current backtest time (any value accepted by
            ``current_time_to_utc``).
        bar_time_to_datetime: caller-supplied function that converts
            a bar's ``t`` field to a tz-aware datetime, or returns
            None for malformed bars.
        current_time_to_utc: caller-supplied function that converts
            ``current_time`` to a tz-aware UTC datetime, or returns
            None for invalid input.
        bar_available_at: caller-supplied function returning the aware UTC
            timestamp when each completed bar becomes observable.
    """
    from event_time import aware_utc

    current_utc = current_time_to_utc(current_time)
    if current_utc is None:
        return {sym: [] for sym in (symbols or [])}
    current_utc = aware_utc(current_utc, field="available_through")

    last_utc = CACHE.get("last_current_utc")
    if last_utc is not None and current_utc < last_utc:
        CACHE["cursors"] = {}
    CACHE["last_current_utc"] = current_utc

    cursors: dict[str, int] = CACHE["cursors"]
    filtered: dict[str, list] = CACHE["filtered_bars"]
    out: dict[str, list] = {}
    for sym in (symbols or []):
        bars = data.get(sym) or []
        n = len(bars)
        if n == 0:
            out[sym] = []
            continue
        # Lazy-init the filtered (malformed-stripped) view per symbol.
        # Bars with unparseable `t` are dropped here once, matching the
        # original `if bt is None: continue` semantic without paying
        # for the check on every call. We track the source-list length
        # so a caller appending to data[sym] can be detected and the
        # filtered list extended.
        f = filtered.get(sym)
        if f is None:
            f = {"entries": [], "src_len": 0}
            filtered[sym] = f
        if f["src_len"] < n:
            # Extend the filtered list with newly-appended bars.
            for j in range(f["src_len"], n):
                bj = bars[j]
                btj = bar_time_to_datetime(bj.get("t"))
                if btj is None:
                    continue
                try:
                    available = aware_utc(
                        bar_available_at(bj),
                        field="bar_available_at",
                    )
                except (TypeError, ValueError):
                    continue
                f["entries"].append((bj, available))
            f["src_len"] = n
        elif f["src_len"] > n:
            # Source shrank; rebuild from scratch.
            f["entries"] = []
            for bj in bars:
                btj = bar_time_to_datetime(bj.get("t"))
                if btj is None:
                    continue
                try:
                    available = aware_utc(
                        bar_available_at(bj),
                        field="bar_available_at",
                    )
                except (TypeError, ValueError):
                    continue
                f["entries"].append((bj, available))
            f["src_len"] = n
            cursors[sym] = 0  # cursor was relative to old length

        clean = f["entries"]
        nf = len(clean)
        idx = cursors.get(sym, 0)
        if idx > nf:
            idx = 0
        while idx < nf:
            if clean[idx][1] <= current_utc:
                idx += 1
            else:
                break
        cursors[sym] = idx
        out[sym] = [bar for bar, _available in clean[:idx]]
    return out
