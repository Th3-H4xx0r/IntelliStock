"""Live-mode daily bars for equity run_once strategies.

WHY: the live main loop passes ``data=None`` to ``run_run_once_strategies`` —
only BACKTEST builds a bars history. Strategy X, XS and EB all read daily closes
from ``data``, so live they see nothing and correctly REFUSE to trade. This
module builds the per-tick ``data`` dict for equity instances from a
broker-injected fetch function.

Import-safe on purpose (no broker import): broker.py is a script with side
effects (argparse at module scope SystemExits under pytest), so the pure logic
lives here where tests can reach it — same pattern as live_crypto_bars.

Safety contract:
- Returns {symbol: [bar, ...]} on success (a symbol may be empty when there is
  nothing yet — the strategy refuses on its own and that is the correct answer).
- A symbol whose fetch came back empty is backfilled from ``last_good``: stale
  bars beat blinding a strategy that is holding a 3x fund.
- Returns ``None`` ONLY when the fetch failed outright and there is no last-good
  snapshot. The caller must then SKIP the tick's run_once strategies.
"""

from __future__ import annotations

import datetime
from typing import Callable, Iterable, Mapping, Optional

#: 400 calendar days ~= 275 trading sessions: more than the 60-bar slow vol
#: window and the 200-bar filters other strategies on this path use, with room
#: for holidays. Cheap — one 1Day request per symbol per tick.
LOOKBACK_DAYS_DEFAULT = 400


def lookback_start(now_utc: datetime.datetime,
                   lookback_days: int = LOOKBACK_DAYS_DEFAULT
                   ) -> datetime.datetime:
    """Fetch-window start: ``lookback_days`` calendar days before ``now_utc``."""
    return now_utc - datetime.timedelta(days=max(1, int(lookback_days)))


def build_live_equity_data(
    fetch_bars: Callable[[list, datetime.datetime, datetime.datetime],
                         Optional[Mapping]],
    symbols: Iterable[str],
    now_utc: datetime.datetime,
    lookback_days: int = LOOKBACK_DAYS_DEFAULT,
    last_good: Optional[Mapping] = None,
    log: Optional[Callable[[str, str], None]] = None,
) -> Optional[dict]:
    """Assemble the live ``data`` dict for equity run_once strategies."""

    def _log(message, color="yellow"):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    syms, seen = [], set()
    for raw in (symbols or []):
        upper = str(raw or "").strip().upper()
        if upper and upper not in seen:
            seen.add(upper)
            syms.append(upper)
    if not syms:
        return {}
    syms.sort()

    start = lookback_start(now_utc, lookback_days)
    try:
        fetched = fetch_bars(syms, start, now_utc)
    except Exception as exc:
        _log(f"Live equity bars fetch raised: {type(exc).__name__}: {exc}",
             "red")
        fetched = None

    if isinstance(fetched, Mapping) and any(fetched.get(s) for s in syms):
        out, stale = {}, []
        for symbol in syms:
            bars = list(fetched.get(symbol) or [])
            if not bars and last_good and last_good.get(symbol):
                bars = list(last_good[symbol])
                stale.append(symbol)
            out[symbol] = bars
        if stale:
            _log("Live equity bars: empty fetch for " + ", ".join(stale)
                 + " — reusing last-good bars (stale) rather than blinding a "
                   "strategy that is holding a levered position.", "yellow")
        return out

    if last_good:
        _log("Live equity bars fetch FAILED — reusing the last-good snapshot "
             "(stale) for this tick.", "red")
        return dict(last_good)
    _log("Live equity bars fetch FAILED with no last-good snapshot — caller "
         "must skip strategies this tick.", "red")
    return None
