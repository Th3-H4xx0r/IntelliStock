"""Bars for next-open fills and bracket legs in equity backtests (spec 6.2).

The simulator fills a `fill_at_next_open` order at a bar's OPEN and checks
bracket legs against each bar's HIGH and LOW. The broker's pending-fill block
only ever hands it the latest CLOSE, so this module supplies the rest: every
completed bar a symbol still needs, oldest first, each stamped with when it
opened and when it became known. It also turns a strategy's sizing hint into
the three `execute_signal` keywords, and only when the hint sets them.

Import-safe on purpose, like `backtest_price_history`: broker.py cannot be
imported under pytest, so the logic lives here and broker.py keeps thin
wrappers.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta, timezone
from typing import Callable

from event_time import aware_utc


#: symbol -> ((id(list), len(list)), labels, source indices). Labels are
#: parsed once per bar list; a list that grows or is replaced is re-parsed.
_LABELS: dict[str, tuple] = {}


def reset_label_cache() -> None:
    """Drop parsed bar labels. Tests call it; production never needs to."""
    _LABELS.clear()


def execution_hint_kwargs(nexus_hint, decision) -> dict:
    """The swing-port `execute_signal` keywords a sizing hint asks for.

    Returns {} unless the hint SETS `fill_at_next_open`, `whole_shares` or
    `bracket` -- so every other strategy's call is exactly what it was.
    `bracket` and `whole_shares` apply to buys only. A `bracket` that is not a
    mapping raises: silently buying without the stop it asked for is worse
    than a loud failure. Its prices are validated by `SimulationOrder`.
    """
    if not isinstance(nexus_hint, dict) or decision not in (1, -1):
        return {}
    out = {}
    if nexus_hint.get("fill_at_next_open") is True:
        out["fill_at_next_open"] = True
    if decision == 1:
        if nexus_hint.get("whole_shares") is True:
            out["whole_shares"] = True
        bracket = nexus_hint.get("bracket")
        if bracket is not None:
            if not isinstance(bracket, dict):
                raise ValueError("bracket hint must be a mapping")
            out["bracket"] = {
                "take_profit_price": bracket.get("take_profit_price"),
                "stop_loss_price": bracket.get("stop_loss_price"),
            }
    return out


def equity_daily_session_open(bar_start: datetime):
    """The NYSE session open for a daily bar's date (aware UTC), or None.

    The open-side twin of broker.py's `_equity_daily_bar_session_close`, and
    None under the same conditions: no calendar library, or not a session.
    """
    # Twin: broker.py _equity_daily_bar_session_close; keep the two in step.
    try:
        import pandas as pd
        import live_calendar

        calendar = getattr(live_calendar, "_CAL", None)
        if not bool(getattr(live_calendar, "_HAS_LIB", False)) or calendar is None:
            return None
        session = pd.Timestamp(bar_start.date())
        if not bool(calendar.is_session(session)):
            return None
        return calendar.session_open(session).tz_convert("UTC").to_pydatetime()
    except Exception:
        return None


def make_bar_open_resolver(
    *,
    interval: timedelta,
    session_open_resolver: Callable[[datetime], datetime | None] | None = None,
) -> Callable[[dict], datetime]:
    """bar -> the instant its first trade could print, aware UTC.

    A daily equity bar is labelled at midnight ET but opens at 09:30 ET; a
    decision at 05:00 PT is BEFORE that open, so the label would put the next
    open a whole session late. Intraday bars open at their label.
    """
    if not isinstance(interval, timedelta) or interval <= timedelta(0):
        raise ValueError("interval must be a positive timedelta")

    def _resolve(bar: dict) -> datetime:
        start = aware_utc(bar.get("t"), field="bar.t")
        if interval >= timedelta(days=1) and session_open_resolver is not None:
            opened = session_open_resolver(start)
            if opened is None:
                raise ValueError("exchange session open is unavailable")
            return aware_utc(opened, field="session_open")
        return start

    return _resolve


def _labels(symbol, bars, bar_time_to_datetime):
    key = (id(bars), len(bars))
    cached = _LABELS.get(symbol)
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    labels, index = [], []
    for position, bar in enumerate(bars):
        label = bar_time_to_datetime((bar or {}).get("t"))
        if label is None:
            continue
        if label.tzinfo is None:
            label = label.replace(tzinfo=timezone.utc)
        labels.append(label.astimezone(timezone.utc))
        index.append(position)
    _LABELS[symbol] = (key, labels, index)
    return labels, index


def collect_bar_events(
    data: dict,
    requirements: dict,
    clock,
    *,
    bar_time_to_datetime: Callable,
    bar_available_at: Callable,
    bar_open_at: Callable,
) -> dict[str, list[dict]]:
    """Every completed bar each symbol still needs, oldest first.

    ``requirements`` is `PortfolioEmulator.bar_event_requirements()`:
    {symbol: earliest bar_ts needed}. A bar is returned when it opened at or
    after that time and is fully known by ``clock`` -- ALL such bars, not just
    the latest, so a weekend, a holiday or a skipped tick loses nothing. A bar
    whose times or prices cannot be read is skipped, as the price cursor
    skips it. A symbol with no such bar (a halt, a delisting) is absent.
    """
    now = aware_utc(clock, field="clock")
    out: dict[str, list[dict]] = {}
    for symbol in sorted(requirements or {}):
        since = aware_utc(requirements[symbol], field="since")
        bars = (data or {}).get(symbol) or []
        if not bars:
            continue
        labels, index = _labels(symbol, bars, bar_time_to_datetime)
        # A bar opens at or after its label and less than a day later.
        start = bisect.bisect_left(labels, since - timedelta(days=1))
        rows = []
        for position in index[start:]:
            bar = bars[position]
            try:
                available = aware_utc(bar_available_at(bar),
                                      field="bar_available_at")
            except (TypeError, ValueError):
                continue
            if available > now:
                break
            try:
                opened = aware_utc(bar_open_at(bar), field="bar_open_at")
                o, h, l, c = (float(bar[k]) for k in ("o", "h", "l", "c"))
            except (KeyError, TypeError, ValueError):
                continue
            if opened < since:
                continue
            rows.append({"t": bar.get("t"), "o": o, "h": h, "l": l, "c": c,
                         "bar_ts": opened, "available_at": available})
        if rows:
            out[symbol] = rows
    return out
