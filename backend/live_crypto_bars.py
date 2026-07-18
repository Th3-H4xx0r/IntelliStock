"""Live-mode crypto bars provisioning for run_once strategies.

WHY: the live main loop historically passed ``data=None`` to
``run_run_once_strategies`` — only BACKTEST built a bars history. Crypto
strategies (meanrev/adaptive/connors/...) rank a universe from ``data``; with
none they see an empty universe and ``core.exit_blind_held`` would risk-off
sell every held coin each tick. This module builds the per-tick ``data`` dict
for crypto instances from a broker-injected fetch function.

Import-safe on purpose (no broker import): broker.py is a script with side
effects, so the pure logic lives here where tests can reach it — same pattern
as broker_session / bar_time / backtest_prices_cursor.

Safety contract:
- Returns a dict {symbol: [bar, ...]} on success (possibly empty when there is
  nothing to fetch yet — discovery bootstraps on the next tick).
- A symbol whose fetch came back empty is backfilled from ``last_good`` (stale
  bars beat blind-exiting a held position on a transient feed hiccup).
- Returns ``None`` ONLY when the fetch failed outright and there is no
  last-good snapshot — the caller must then SKIP the tick's strategies
  entirely (running them with no data would liquidate the book).
"""

from __future__ import annotations

import datetime
from typing import Callable, Iterable, Mapping, Optional


def lookback_start(now_utc: datetime.datetime, increment_seconds: int,
                   lookback_bars: int) -> datetime.datetime:
    """Fetch-window start: lookback_bars * increment before now (plus one bar
    of slack so the newest partial bar never shorts the window)."""
    secs = max(1, int(increment_seconds)) * (max(1, int(lookback_bars)) + 1)
    return now_utc - datetime.timedelta(seconds=secs)


def build_live_crypto_data(
    fetch_bars: Callable[[list, datetime.datetime, datetime.datetime], Optional[Mapping]],
    symbols: Iterable[str],
    held: Iterable[str],
    discovered: Iterable[str],
    default_majors: Iterable[str],
    now_utc: datetime.datetime,
    increment_seconds: int,
    lookback_bars: int,
    last_good: Optional[Mapping] = None,
    log: Optional[Callable[[str, str], None]] = None,
) -> Optional[dict]:
    """Assemble the live ``data`` dict for crypto run_once strategies.

    Universe = configured symbols ∪ held positions ∪ previously discovered
    coins; when all three are empty (cold start of a 100%-dynamic instance)
    fall back to ``default_majors`` so the first tick already has bars.
    """
    def _log(msg, color="yellow"):
        if log is not None:
            try:
                log(msg, color)
            except Exception:
                pass

    syms = []
    seen = set()
    for group in (symbols, held, discovered):
        for s in group or []:
            u = str(s).strip().upper()
            if u and u not in seen:
                seen.add(u)
                syms.append(u)
    if not syms:
        syms = [str(s).strip().upper() for s in (default_majors or []) if str(s).strip()]
    if not syms:
        return {}

    start = lookback_start(now_utc, increment_seconds, lookback_bars)
    fetched = None
    try:
        fetched = fetch_bars(sorted(syms), start, now_utc)
    except Exception as e:
        _log(f"Live crypto bars fetch raised: {type(e).__name__}: {e}", "red")
        fetched = None

    ok = isinstance(fetched, Mapping) and any(fetched.get(s) for s in syms)
    if ok:
        out = {}
        stale = []
        for s in syms:
            bars = list(fetched.get(s) or [])
            if not bars and last_good and last_good.get(s):
                bars = list(last_good[s])
                stale.append(s)
            out[s] = bars
        if stale:
            _log(
                f"Live crypto bars: empty fetch for {', '.join(stale)} — "
                f"reusing last-good bars (stale) so held positions aren't blind-exited.",
                "yellow",
            )
        return out

    if last_good:
        _log(
            "Live crypto bars fetch FAILED — reusing the last-good snapshot "
            "(stale) for this tick.",
            "red",
        )
        return dict(last_good)
    _log(
        "Live crypto bars fetch FAILED with no last-good snapshot — caller "
        "must skip strategies this tick.",
        "red",
    )
    return None
