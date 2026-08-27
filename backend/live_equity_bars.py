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
- A symbol whose fetch came back empty — or SHORTER than the snapshot already
  held — is served from ``last_good``: stale bars beat blinding, or half-blinding,
  a strategy that is holding a 3x fund.
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


#: `strategy_eb`'s two accepted spellings, matching
#: `broker._strategy_eb_universe_symbols`.
EB_STRATEGY_NAMES = frozenset({"strategy_eb", "strategyeb"})


def other_enabled_run_once_lanes(run_once_specs) -> list:
    """Names of run_once lanes on this document that are NOT strategy_eb.

    Design spec §8 says the EB document must carry no other enabled lane. That
    is a LIVE CORRECTNESS rule, not a preference, which is why it is enforced in
    code: `graph_nexus_analysis` discriminates live from backtest on
    ``data is not None`` (`_bz_set_bt_main(data is not None)`), so the moment
    this module hands real bars to a document that also runs GNA, GNA flips into
    BACKTEST budget mode on a live tick. Any non-empty result means the caller
    must leave ``data=None``.

    A lane counts as enabled unless its own config explicitly says otherwise
    (``<name>_enabled`` or ``enabled`` present and falsy). Absent means enabled:
    most run_once strategies — GNA among them — have no enable key at all, and
    the failure this guards is silent, so the guard fails CLOSED.
    """
    out = []
    for spec in (run_once_specs or []):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("strategy") or "").strip().lower()
        if not name or name in EB_STRATEGY_NAMES:
            continue
        config = spec.get("config") or {}
        disabled = False
        if isinstance(config, Mapping):
            for key in (f"{name}_enabled", "enabled"):
                if key in config and not _truthy(config.get(key)):
                    disabled = True
                    break
        if not disabled and name not in out:
            out.append(name)
    return out


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
        out, stale, truncated = {}, [], []
        for symbol in syms:
            bars = list(fetched.get(symbol) or [])
            held = list((last_good or {}).get(symbol) or [])
            if held and len(bars) < len(held):
                # An EMPTY series is the obvious case, but not the dangerous
                # one. `fetch_alpaca_historical_bars` stitches the window from
                # chunks and swallows a failed chunk, so a partial outage
                # returns a SHORT series — 24 closes instead of 275 — which is
                # not an error anywhere. A shorter window on the same tape
                # measures less realised volatility, and less measured risk
                # sizes the 3x core LARGER: the one direction this path must
                # never fail in. The window only ever grows, so shorter than
                # what we already hold means degraded, not new.
                (stale if not bars else truncated).append(symbol)
                bars = held
            out[symbol] = bars
        if stale:
            _log("Live equity bars: empty fetch for " + ", ".join(stale)
                 + " — reusing last-good bars (stale) rather than blinding a "
                   "strategy that is holding a levered position.", "yellow")
        if truncated:
            _log("Live equity bars: TRUNCATED fetch for " + ", ".join(truncated)
                 + " — reusing the longer last-good series (stale). A short "
                   "window under-measures volatility, and under-measured risk "
                   "sizes a levered position LARGER.", "yellow")
        return out

    if last_good:
        _log("Live equity bars fetch FAILED — reusing the last-good snapshot "
             "(stale) for this tick.", "red")
        return dict(last_good)
    _log("Live equity bars fetch FAILED with no last-good snapshot — caller "
         "must skip strategies this tick.", "red")
    return None
