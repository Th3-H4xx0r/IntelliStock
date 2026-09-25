"""The swing lane's own daily bars in a backtest.

The engine fetches bars up front only for the instance's watchlist. The swing
lane's universe is the S&P 500 membership visible in the run's window (~600
names, spec §7), so the lane fetches its own daily bars for it, once, on its
first session. No engine change: a symbol the lane then trades is loaded by
the engine's on-demand loader (broker.py `_ensure_backtest_history_for_symbols`)
for pricing and fills.

Same source as that loader, so signals and fills price off identical bars:
broker.fetch_alpaca_historical_bars asks Alpaca's /v2/stocks/{symbol}/bars
with adjustment "split", the instance's data feed, limit 10000, sorted
ascending, in 365-day chunks for 1Day bars, through the Postgres
AlpacaBarsCache (price_utils.get_bars_chunk_cached), which makes a repeated
window cheap. broker.py cannot be imported (it argparses at module scope), so
the request is rebuilt here and pinned by test_swing_backtest_bars.py.

OwnBars indexes the bars by session date, so each tick hands the indicator
code only the sessions strictly before the one being decided (no
look-ahead), and merged_view lets the engine's bars win for every symbol the
engine carries, which leaves a watchlist symbol exactly as it was.
"""
from __future__ import annotations

import bisect
import time
from datetime import date, datetime, timedelta

from swing_trader import clock

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingBacktestBars")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingBacktestBars] {msg}", flush=True)

ALPACA_DATA_BASE = "https://data.alpaca.markets/v2"
TIMEFRAME = "1Day"
#: broker.fetch_alpaca_historical_bars' default adjustment.
ADJUSTMENT = "split"
#: broker._alpaca_chunk_days_for_timeframe("1Day").
CHUNK_DAYS = 365
PAGE_LIMIT = 10000
MAX_PAGES = 500
MAX_429_RETRIES = 3
_TAG = "[swing-bars]"
#: price_utils.get_bars_chunk_cached reads its handle only as "not None".
_CACHE_HANDLE = True


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_get(url, *, headers, params, timeout):
    import requests
    return requests.get(url, headers=headers, params=params, timeout=timeout)


def _fetch_chunk(symbol, chunk_start, chunk_end, *, key, secret, feed, http_get, sleep):
    """One chunk from Alpaca, paginated, with the loader's 429 back-off."""
    params = {"start": _iso(chunk_start), "end": _iso(chunk_end), "timeframe": TIMEFRAME,
              "limit": PAGE_LIMIT, "sort": "asc", "feed": feed, "adjustment": ADJUSTMENT}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
               "accept": "application/json"}
    url = f"{ALPACA_DATA_BASE}/stocks/{symbol}/bars"
    collected, throttled = [], 0
    for _page in range(MAX_PAGES):
        response = http_get(url, headers=headers, params=params, timeout=60)
        if getattr(response, "status_code", 200) == 429 and throttled < MAX_429_RETRIES:
            throttled += 1
            try:
                wait = max(1.0, min(30.0, float(response.headers.get("Retry-After", "2"))))
            except (TypeError, ValueError, AttributeError):
                wait = 2.0 * throttled
            sleep(wait)
            continue
        response.raise_for_status()
        body = response.json() or {}
        collected.extend(body.get("bars") or [])
        token = body.get("next_page_token")
        if not token:
            break
        params = {k: v for k, v in params.items() if k not in ("start", "end")}
        params["page_token"] = token
    return collected


def fetch_daily_bars(symbols, start, end, *, key, secret, feed, http_get=None,
                     cached=True, sleep=time.sleep, log=None) -> dict:
    """{symbol: [bar, ...]} of 1Day bars over [start, end] (dates, both
    inclusive), sorted and de-duplicated by "t". Best-effort: a chunk that
    fails is logged and skipped, and a symbol with no bars is absent."""
    log = _log if log is None else log
    symbols = sorted({str(s).strip().upper() for s in (symbols or []) if str(s).strip()})
    if not symbols:
        return {}
    if not key or not secret:
        log(f"{_TAG} no Alpaca data credentials in the lane's config; the lane runs on "
            "the engine's bars only", "yellow")
        return {}
    feed = str(feed or "iex").strip().lower()
    feed = feed if feed in ("iex", "sip") else "iex"
    http_get = http_get or _http_get
    get_cached = None
    if cached:
        try:
            from price_utils import get_bars_chunk_cached as get_cached
        except Exception:
            get_cached = None
    start_dt = datetime(start.year, start.month, start.day)
    # The engine's inclusive end: the last day's 23:59:59.
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59)
    t0, out, failed = time.monotonic(), {}, []
    for n, sym in enumerate(symbols, 1):
        bars, chunk_start = [], start_dt
        while chunk_start < end_dt:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end_dt)

            def fetch(sym=sym, a=chunk_start, b=chunk_end):
                return _fetch_chunk(sym, a, b, key=key, secret=secret, feed=feed,
                                    http_get=http_get, sleep=sleep)
            try:
                if get_cached is not None:
                    got, _hit = get_cached(_CACHE_HANDLE, sym, chunk_start, chunk_end,
                                           TIMEFRAME, feed, fetch, adjustment=ADJUSTMENT)
                else:
                    got = fetch()
                bars.extend(got or [])
            except Exception as exc:
                failed.append(f"{sym} {chunk_start.date()} ({type(exc).__name__}: {exc})")
            chunk_start = chunk_end
        seen, unique = set(), []
        for bar in sorted((b for b in bars if isinstance(b, dict)),
                          key=lambda b: str(b.get("t") or "")):
            t = bar.get("t")
            if t and t not in seen:
                seen.add(t)
                unique.append(bar)
        if unique:
            out[sym] = unique
        if n % 100 == 0:
            log(f"{_TAG} {n}/{len(symbols)} symbols fetched", "cyan")
    if failed:
        log(f"{_TAG} {len(failed)} chunk(s) failed and were skipped: "
            + "; ".join(failed[:5]), "yellow")
    log(f"{_TAG} own daily bars {start}..{end} ({feed}, {ADJUSTMENT}): "
        f"{len(out)}/{len(symbols)} symbols, {sum(len(v) for v in out.values())} bars, "
        f"{time.monotonic() - t0:.1f}s", "cyan")
    return out


class OwnBars:
    """Daily bars indexed by session date (the UTC date of the bar's label,
    which is the session for Alpaca's midnight-ET daily stamps, as
    market_data.frames_from_engine_bars reads it)."""

    def __init__(self, bars_by_symbol=None):
        self._days, self._bars = {}, {}
        for sym, bars in (bars_by_symbol or {}).items():
            stamped = []
            for bar in bars or []:
                if not isinstance(bar, dict):
                    continue
                ts = clock.as_utc(bar.get("t") or bar.get("timestamp") or bar.get("date"))
                if ts is not None:
                    stamped.append((ts.date().isoformat(), str(bar.get("t")), bar))
            if stamped:
                stamped.sort(key=lambda item: (item[0], item[1]))
                self._days[str(sym).upper()] = [d for d, _t, _b in stamped]
                self._bars[str(sym).upper()] = [b for _d, _t, b in stamped]

    @property
    def symbols(self) -> list:
        return sorted(self._bars)

    def before(self, symbol, session, lookback_days) -> list:
        """The bars dated in [session - lookback_days, session): never the
        session itself or anything after it."""
        days = self._days.get(symbol)
        if not days:
            return []
        cutoff = str(session)[:10]
        floor = (date.fromisoformat(cutoff) - timedelta(days=int(lookback_days))).isoformat()
        lo, hi = bisect.bisect_left(days, floor), bisect.bisect_left(days, cutoff)
        return list(self._bars[symbol][lo:hi])


def has_bars(entry) -> bool:
    """True when an engine `data` entry holds bars. The engine hands bars as
    {sym: [...]} or {sym: {"bars": [...]}}."""
    if isinstance(entry, dict):
        return bool(entry.get("bars"))
    return bool(entry)


def merged_view(data, own, session, lookback_days) -> dict:
    """The engine's `data`, plus the lane's own bars before `session` for
    every symbol the engine carries no bars for. The engine's bars win."""
    view = dict(data) if isinstance(data, dict) else {}
    if own is None:
        return view
    for sym in own.symbols:
        if not has_bars(view.get(sym)):
            view[sym] = own.before(sym, session, lookback_days)
    return view
