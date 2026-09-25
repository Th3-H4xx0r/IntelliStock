"""The swing lane's own daily bars in a backtest.

The engine fetches bars up front only for the instance's watchlist. The swing
lane's universe is the S&P 500 membership visible in the run's window (~600
names, spec §7), so the lane fetches its own daily bars for it, once, on its
first session. No engine change: a symbol the lane then trades is loaded by
the engine's on-demand loader (broker.py `_ensure_backtest_history_for_symbols`)
for pricing and fills.

Same source as that loader, so signals and fills price off identical bars:
broker.fetch_alpaca_historical_bars asks Alpaca for 1Day bars with
adjustment "split", the instance's data feed, limit 10000, sorted ascending.
broker.py cannot be imported (it argparses at module scope), so the request
is rebuilt here and its params are pinned by the tests.

The bars are cached in SwingDailyBars, one row per symbol per calendar year
("SYM|YYYY", with the span it covers). A run reads every row it needs in a
few batched queries, fetches only what is missing -- years with no row, and
the head and tail of each row's coverage -- with multi-symbol requests of up
to 100 symbols, and writes back only the rows that changed. Each gap fill
reaches a few sessions into the cached span; a cached close that moved there
means a split rewrote history, and that symbol's history is refetched. When
SwingDailyBars cannot be read, the per-symbol path (one request per symbol
and 365-day chunk, through the engine's AlpacaBarsCache) is the fallback.

OwnBars indexes the bars by session date, so each tick hands the indicator
code only the sessions strictly before the one being decided (no
look-ahead), and merged_view lets the engine's bars win for every symbol the
engine carries, which leaves a watchlist symbol exactly as it was.
"""
from __future__ import annotations

import bisect
import time
from datetime import date, datetime, timedelta, timezone

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
#: Operator requirement: a progress line every 10 symbols loaded.
PROGRESS_EVERY = 10
#: The lane's own daily-bar cache: one row per symbol per calendar year.
CACHE_TABLE = "SwingDailyBars"      # refdata.BARS_TABLE
#: Symbols per multi-symbol request (Alpaca's /v2/stocks/bars).
MULTI_SYMBOLS = 100
#: Calendar days (about 5 sessions) a gap fill reaches into cached coverage,
#: so the split check has bars to compare.
OVERLAP_DAYS = 7
#: A cached close that moved by more than this on refetch: a split or an
#: adjustment change rewrote history.
SPLIT_TOLERANCE = 0.005
READ_BATCH = 1000
WRITE_CHUNK = 200
_TAG = "[swing-bars]"
#: price_utils.get_bars_chunk_cached reads its handle only as "not None".
_CACHE_HANDLE = True


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration(seconds) -> str:
    """"2m40s", "45s", "1h02m": a short wall-clock span for progress lines."""
    try:
        seconds = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "?"
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"


class Progress:
    """Logs "n/total symbols, rate/s, ETA" every `every` symbols and at the
    last one (operator requirement: every 10), so a long load never looks
    stuck. Logging only: it never raises into the caller."""

    def __init__(self, total, log, *, every=PROGRESS_EVERY, clock=time.monotonic,
                 tag=_TAG, noun="symbols"):
        self.total, self.log, self.every, self.clock = int(total), log, int(every), clock
        self.tag, self.noun = tag, noun
        self.t0 = clock()

    def line(self, n, extra="") -> str:
        elapsed = max(self.clock() - self.t0, 1e-9)
        rate = n / elapsed
        eta = (self.total - n) / rate if rate > 0 else 0.0
        return (f"{self.tag} {n}/{self.total} {self.noun}, {rate:.1f}/s, "
                f"ETA {_duration(eta)}" + (f" | {extra}" if extra else ""))

    def step(self, n, extra=""):
        if n % self.every and n != self.total:
            return
        try:
            self.log(self.line(n, extra), "cyan")
        except Exception:  # pragma: no cover - logging never breaks the load
            pass


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
                     cached=True, sleep=time.sleep, log=None, clock=time.monotonic,
                     store=None, today=None) -> dict:
    """{symbol: [bar, ...]} of 1Day bars over [start, end] (dates, both
    inclusive), sorted and de-duplicated by "t". Best-effort: a range that
    fails is logged and skipped, and a symbol with no bars is absent.

    cached (the default): through the SwingDailyBars cache -- one batched
    read, then only the missing ranges, fetched with multi-symbol requests
    (see _load_through_cache). When that table cannot be read, the
    per-symbol path through AlpacaBarsCache, logged yellow. cached=False:
    the per-symbol path with no cache at all."""
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
    if cached:
        try:
            return _load_through_cache(symbols, start, end, key=key, secret=secret, feed=feed,
                                       http_get=http_get, sleep=sleep, log=log, clock=clock,
                                       store=store, today=today)
        except CacheUnreadable as exc:
            log(f"{_TAG} {CACHE_TABLE} unreadable ({exc}); falling back to the per-symbol "
                "path", "yellow")
    return _fetch_per_symbol(symbols, start, end, key=key, secret=secret, feed=feed,
                             http_get=http_get, cached=cached, sleep=sleep, log=log,
                             clock=clock)


def _log_done(log, out, symbols, start, end, feed, tail):
    missing = [s for s in symbols if s not in out]
    days = [str(b["t"])[:10] for v in out.values() for b in (v[0], v[-1])]
    span = f"{min(days)}..{max(days)}" if days else "none"
    log(f"{_TAG} bars phase done: own daily bars {start}..{end} ({feed}, {ADJUSTMENT}): "
        f"{len(out)}/{len(symbols)} symbols with bars, {sum(len(v) for v in out.values())} "
        f"bars dated {span}; {len(missing)} with none"
        + (f" ({', '.join(missing[:10])}{', ...' if len(missing) > 10 else ''})"
           if missing else "")
        + f"; {tail}", "cyan")


# -- the SwingDailyBars cache ------------------------------------------------

class CacheUnreadable(RuntimeError):
    """SwingDailyBars could not be read: the caller takes the per-symbol path."""


def _d(value) -> date:
    return date.fromisoformat(str(value)[:10])


def _multi_params(start_d, end_d, feed, symbols, page_token=None) -> dict:
    """The engine loader's params (_historical_bars_request_params: same
    timeframe, limit, sort, feed and adjustment) plus the symbol list."""
    params = {"start": _iso(datetime(start_d.year, start_d.month, start_d.day)),
              "end": _iso(datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59)),
              "timeframe": TIMEFRAME, "limit": PAGE_LIMIT, "sort": "asc", "feed": feed,
              "adjustment": ADJUSTMENT, "symbols": ",".join(symbols)}
    if page_token:
        # The next page repeats the request with the token, as alpaca-py does.
        params["page_token"] = page_token
    return params


def _fetch_multi(symbols, start_d, end_d, *, key, secret, feed, http_get, sleep) -> dict:
    """{symbol: [bar, ...]} for up to MULTI_SYMBOLS symbols over [start_d,
    end_d], following next_page_token, with the loader's 429 back-off."""
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
               "accept": "application/json"}
    url = f"{ALPACA_DATA_BASE}/stocks/bars"
    params = _multi_params(start_d, end_d, feed, symbols)
    out, throttled = {s: [] for s in symbols}, 0
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
        for sym, bars in (body.get("bars") or {}).items():
            out.setdefault(str(sym).upper(), []).extend(b for b in bars or []
                                                        if isinstance(b, dict))
        token = body.get("next_page_token")
        if not token:
            break
        params = _multi_params(start_d, end_d, feed, symbols, page_token=token)
    return out


def _gaps(rows, lo, hi):
    """The date ranges [a, b] of one symbol missing from its rows ({year:
    row}) over [lo, hi]: years with no row, and the head and tail of each
    row's coverage, each kept contiguous with that coverage. Adjacent ranges
    merge. A range that touches cached coverage reaches OVERLAP_DAYS into it,
    so the split check has bars to compare."""
    one = timedelta(days=1)
    ranges = []
    for y in range(lo.year, hi.year + 1):
        need_lo, need_hi = max(lo, date(y, 1, 1)), min(hi, date(y, 12, 31))
        row = rows.get(y)
        if row is None:
            ranges.append([need_lo, need_hi])
            continue
        cf, ct = _d(row["covered_from"]), _d(row["covered_through"])
        if need_lo < cf:
            ranges.append([need_lo, cf - one])
        if need_hi > ct:
            ranges.append([ct + one, need_hi])
    merged = []
    for a, b in sorted(ranges):
        if merged and a <= merged[-1][1] + one:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    def covered(day):
        row = rows.get(day.year)
        return row is not None and _d(row["covered_from"]) <= day <= _d(row["covered_through"])

    out = []
    for a, b in merged:
        if covered(a - one):
            a = a - timedelta(days=OVERLAP_DAYS)
        if covered(b + one):
            b = b + timedelta(days=OVERLAP_DAYS)
        out.append((a, b))
    return out


def _split_changed(cached_by_t, fresh) -> bool:
    """A fetched bar whose close differs from the cached bar of the same "t"
    by more than SPLIT_TOLERANCE: the adjustment rewrote history."""
    for bar in fresh:
        old = cached_by_t.get(bar.get("t"))
        if old is None:
            continue
        try:
            a, b = float(old.get("c")), float(bar.get("c"))
        except (TypeError, ValueError):
            continue
        if a > 0 and abs(b / a - 1.0) > SPLIT_TOLERANCE:
            return True
    return False


def _read_rows(st, symbols, years):
    """{symbol: {year: row}} in as few queries as possible (get_all by id,
    READ_BATCH ids a query). Raises CacheUnreadable."""
    ids = [f"{s}|{y}" for s in symbols for y in years]
    rows = {}
    try:
        for k in range(0, len(ids), READ_BATCH):
            for row in st.get_all(CACHE_TABLE, *ids[k:k + READ_BATCH]) or []:
                sym, _, year = str(row.get("id") or "").partition("|")
                rows.setdefault(sym, {})[int(year)] = row
    except Exception as exc:
        raise CacheUnreadable(f"{type(exc).__name__}: {exc}") from exc
    return rows


def _load_through_cache(symbols, start, end, *, key, secret, feed, http_get, sleep, log,
                        clock, store=None, today=None) -> dict:
    """The SwingDailyBars path of fetch_daily_bars. Coverage stops at the
    last final session (yesterday, NY): a bar of today or later is never
    cached or needed (the lane reads only sessions before the one it
    decides). Rows of another feed or adjustment read as absent."""
    if store is None:
        try:
            from db import store as st
            from swing_trader import signals_store
            signals_store.ensure_tables()      # DDL stays in db/schema.py
        except Exception as exc:
            raise CacheUnreadable(f"tables unavailable: {type(exc).__name__}: {exc}") from exc
    else:
        st = store
    t0 = clock()
    today = _d(today) if today is not None else _d(clock_ny_today())
    lo, hi = start, min(end, today - timedelta(days=1))
    years = list(range(lo.year, hi.year + 1)) if hi >= lo else []
    rows = _read_rows(st, symbols, years)
    for sym in list(rows):
        rows[sym] = {y: r for y, r in rows[sym].items()
                     if r.get("feed") == feed and r.get("adjustment") == ADJUSTMENT}
    n_rows = sum(len(v) for v in rows.values())
    todo = {s: _gaps(rows.get(s, {}), lo, hi) for s in symbols} if years else {}
    todo = {s: g for s, g in todo.items() if g}
    cold = sum(1 for s in todo if not rows.get(s))
    log(f"{_TAG} {CACHE_TABLE}: {n_rows} row(s) read for {len(symbols)} symbols x "
        f"{len(years)} year(s) in {clock() - t0:.2f}s; {len(symbols) - len(todo)} fully "
        f"covered, {len(todo)} need gap fill ({cold} cold)", "cyan")

    stats = {"requests": 0, "throttled": 0, "waited": 0.0}
    failed = []

    def counted_sleep(seconds):
        stats["throttled"] += 1
        try:
            stats["waited"] += float(seconds)
        except (TypeError, ValueError):
            pass
        return sleep(seconds)

    def fetch_groups(ranges_by_symbol, on_done):
        """Group symbols by identical range, MULTI_SYMBOLS a request."""
        groups = {}
        for sym, ranges in ranges_by_symbol.items():
            for r in ranges:
                groups.setdefault(r, []).append(sym)
        left = {s: len(r) for s, r in ranges_by_symbol.items()}
        got = {}
        for (a, b), syms in sorted(groups.items()):
            for k in range(0, len(syms), MULTI_SYMBOLS):
                batch = sorted(syms[k:k + MULTI_SYMBOLS])
                stats["requests"] += 1
                try:
                    bars = _fetch_multi(batch, a, b, key=key, secret=secret, feed=feed,
                                        http_get=http_get, sleep=counted_sleep)
                except Exception as exc:
                    failed.append(f"{len(batch)} symbol(s) {batch[0]}.. {a}..{b} "
                                  f"({type(exc).__name__}: {exc})")
                    bars = None
                for sym in batch:
                    if bars is not None:
                        got.setdefault(sym, []).append(((a, b), bars.get(sym) or []))
                    left[sym] -= 1
                    if not left[sym]:
                        on_done(sym)
        return got

    groups = len({r for g in todo.values() for r in g})
    if todo:
        log(f"{_TAG} gap fill: {len(todo)} symbol(s) in {groups} distinct range(s), up to "
            f"{MULTI_SYMBOLS} symbols a request; progress every {PROGRESS_EVERY} symbols",
            "cyan")
    progress = Progress(len(todo), log, clock=clock)
    done = [0]

    def tick(_sym):
        done[0] += 1
        progress.step(done[0], f"requests {stats['requests']}, 429 back-offs "
                               f"{stats['throttled']} ({stats['waited']:.0f}s), failed "
                               f"{len(failed)}")

    fetched = fetch_groups(todo, tick)

    # Split check: a fetched bar that overlaps a cached one with a different
    # close means the adjustment rewrote the symbol's history.
    stale = []
    for sym, parts in fetched.items():
        cached_by_t = {b.get("t"): b for r in rows.get(sym, {}).values() for b in r.get("bars") or []}
        if cached_by_t and any(_split_changed(cached_by_t, bars) for _r, bars in parts):
            stale.append(sym)
    if stale:
        for sym in stale:
            log(f"{_TAG} split/adjustment change detected for {sym} — history refreshed",
                "yellow")
        again = fetch_groups({s: [(lo, hi)] for s in stale}, lambda _s: None)
        for sym in stale:
            fetched[sym] = again.get(sym, [])
            rows[sym] = {}
            try:
                st.delete(CACHE_TABLE, st.between(CACHE_TABLE, f"{sym}|", f"{sym}|~"))
            except Exception as exc:
                log(f"{_TAG} {sym}: stale rows not deleted ({type(exc).__name__}: {exc})",
                    "yellow")

    # Merge the fetched ranges into the rows; write back only what changed.
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    writes = []
    for sym, parts in fetched.items():
        old = rows.get(sym, {})
        by_t = {b.get("t"): b for r in old.values() for b in r.get("bars") or []}
        cover = {y: [_d(r["covered_from"]), _d(r["covered_through"])] for y, r in old.items()}
        touched = set()
        for (a, b), bars in parts:
            for bar in bars:
                t = bar.get("t")
                if t and _d(t) <= hi:
                    by_t[t] = bar
            for y in range(a.year, b.year + 1):
                ya, yb = max(a, date(y, 1, 1)), min(b, date(y, 12, 31), hi)
                if ya > yb:
                    continue
                c = cover.get(y)
                cover[y] = [min(c[0], ya), max(c[1], yb)] if c else [ya, yb]
                touched.add(y)
        by_year = {}
        for t, bar in by_t.items():
            by_year.setdefault(int(str(t)[:4]), []).append(bar)
        for y in sorted(touched):
            bars = sorted(by_year.get(y, []), key=lambda b: str(b.get("t") or ""))
            prior = old.get(y)
            if (prior is not None and prior.get("bars") == bars
                    and prior.get("covered_from") == cover[y][0].isoformat()
                    and prior.get("covered_through") == cover[y][1].isoformat()):
                continue            # an overlap read that changed nothing
            doc = {"id": f"{sym}|{y}", "symbol": sym, "year": y, "bars": bars,
                   "covered_from": cover[y][0].isoformat(),
                   "covered_through": cover[y][1].isoformat(), "feed": feed,
                   "adjustment": ADJUSTMENT, "fetched_at": now_iso}
            rows.setdefault(sym, {})[y] = doc
            writes.append(doc)
    if writes:
        try:
            writer = getattr(st, "insert_bulk", None)
            if callable(writer):
                writer(CACHE_TABLE, writes, conflict="replace", chunk=WRITE_CHUNK)
            else:
                st.insert(CACHE_TABLE, writes, conflict="replace")
        except Exception as exc:
            log(f"{_TAG} {CACHE_TABLE} write failed ({type(exc).__name__}: {exc}); this run "
                "uses the fetched bars, the next one fetches them again", "yellow")

    out = {}
    for sym in symbols:
        seen, unique = set(), []
        for r in sorted(rows.get(sym, {}).values(), key=lambda r: r["year"]):
            for bar in r.get("bars") or []:
                t = bar.get("t")
                if t and t not in seen and start <= _d(t) <= end:
                    seen.add(t)
                    unique.append(bar)
        if unique:
            out[sym] = sorted(unique, key=lambda b: str(b.get("t") or ""))
    if failed:
        log(f"{_TAG} {len(failed)} request(s) failed and were skipped: "
            + "; ".join(failed[:5]) + (f"; +{len(failed) - 5} more" if len(failed) > 5 else ""),
            "yellow")
    if stats["throttled"]:
        log(f"{_TAG} Alpaca rate limit: {stats['throttled']} 429 back-off(s), "
            f"{stats['waited']:.0f}s waited in total", "yellow")
    _log_done(log, out, symbols, start, end, feed,
              f"{CACHE_TABLE} rows read {n_rows}, written {len(writes)}; batched requests "
              f"{stats['requests']}; split refreshes {len(stale)}; {clock() - t0:.1f}s")
    return out


def clock_ny_today() -> str:
    """Today's NY date (a seam for the tests)."""
    return clock.ny_date(datetime.now(timezone.utc))


def _fetch_per_symbol(symbols, start, end, *, key, secret, feed, http_get, cached, sleep,
                      log, clock) -> dict:
    """The per-symbol path: one request per symbol and 365-day chunk, through
    the engine's AlpacaBarsCache. The fallback when SwingDailyBars cannot be
    read."""
    get_cached = None
    if cached:
        try:
            from price_utils import get_bars_chunk_cached as get_cached
        except Exception:
            get_cached = None
    start_dt = datetime(start.year, start.month, start.day)
    # The engine's inclusive end: the last day's 23:59:59.
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59)
    t0, out, failed = clock(), {}, []
    stats = {"hits": 0, "network": 0, "throttled": 0, "waited": 0.0}

    def counted_sleep(seconds):
        # Logging only: the same wait, counted for the progress lines.
        stats["throttled"] += 1
        try:
            stats["waited"] += float(seconds)
        except (TypeError, ValueError):
            pass
        return sleep(seconds)

    def tally():
        return (f"cache hits {stats['hits']}, network {stats['network']}, "
                f"429 back-offs {stats['throttled']} ({stats['waited']:.0f}s), "
                f"failed chunks {len(failed)}")

    chunks = 0
    probe = start_dt
    while probe < end_dt:
        chunks += 1
        probe = min(probe + timedelta(days=CHUNK_DAYS), end_dt)
    log(f"{_TAG} bars phase start: {len(symbols)} symbols, {start}..{end} ({feed}, "
        f"{ADJUSTMENT}), {chunks} chunk(s) of {CHUNK_DAYS} days each, "
        f"{'through the bars cache' if get_cached is not None else 'no bars cache'}; "
        f"progress every {PROGRESS_EVERY} symbols", "cyan")
    progress = Progress(len(symbols), log, clock=clock)
    for n, sym in enumerate(symbols, 1):
        bars, chunk_start = [], start_dt
        while chunk_start < end_dt:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end_dt)

            def fetch(sym=sym, a=chunk_start, b=chunk_end):
                return _fetch_chunk(sym, a, b, key=key, secret=secret, feed=feed,
                                    http_get=http_get, sleep=counted_sleep)
            try:
                if get_cached is not None:
                    got, _hit = get_cached(_CACHE_HANDLE, sym, chunk_start, chunk_end,
                                           TIMEFRAME, feed, fetch, adjustment=ADJUSTMENT)
                    stats["hits" if _hit else "network"] += 1
                else:
                    stats["network"] += 1
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
        progress.step(n, tally())
    if failed:
        log(f"{_TAG} {len(failed)} chunk(s) failed and were skipped: "
            + "; ".join(failed[:5]) + (f"; +{len(failed) - 5} more" if len(failed) > 5 else ""),
            "yellow")
    if stats["throttled"]:
        log(f"{_TAG} Alpaca rate limit: {stats['throttled']} 429 back-off(s), "
            f"{stats['waited']:.0f}s waited in total", "yellow")
    _log_done(log, out, symbols, start, end, feed, f"{tally()}; {clock() - t0:.1f}s")
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
