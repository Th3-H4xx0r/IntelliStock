"""
Price/bar data utilities for IntelliStock.
Alpaca bars cache: cache bars by (symbol, date range, timeframe, feed) in Postgres
to avoid repeated API calls and 429 rate limits.
Large payloads are gzip-compressed to keep the cached document small.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from datetime import datetime, timedelta

# A cached chunk is treated as stale (and refetched) when its last bar is
# more than this many days before the chunk's end_date. The historical reason
# this matters: if the chunk was first fetched while end_date was still in
# the future (or while the broker was using credentials that returned a
# truncated window — e.g. a paper account), the cache can lock in months of
# missing data forever. Seven days covers long weekends + holidays for daily
# bars without re-fetching every time on minor gaps.
_CACHE_COVERAGE_TOLERANCE_DAYS = 7

# Compress bars JSON when larger than this to keep the cached document small
_BARS_COMPRESS_THRESHOLD = 400_000

try:
    from db import schema as _db_schema
    from db import store as _store
except Exception:
    _store = None
    _db_schema = None

DEFAULT_DB_NAME = "IntelliStock"
ALPACA_BARS_CACHE_TABLE = "AlpacaBarsCache"
_table_ensured: dict[str, bool] = {}


def _to_date_str(dt) -> str:
    """Normalize to YYYY-MM-DD for cache key."""
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d")
    s = str(dt)
    if "T" in s:
        s = s[:10]
    return s[:10] if len(s) >= 10 else s


def alpaca_bars_cache_key(symbol: str, start_dt, end_dt, timeframe: str,
                          feed: str, adjustment: str = "raw") -> str:
    """
    Deterministic cache key for one chunk of Alpaca bars.
    Same (symbol, start, end, timeframe, feed, adjustment) yields the same key.

    2026-08-02: `adjustment` is part of the identity. A raw chunk and a
    split-adjusted chunk of the same window are DIFFERENT data, and serving one
    for the other reintroduces exactly the unadjusted-split corruption this
    cache was silently laundering (VGT 8-for-1 read as an 87% crash). Old raw
    rows simply become unreachable rather than wrong — no purge required.
    """
    start_s = _to_date_str(start_dt)
    end_s = _to_date_str(end_dt)
    adj = str(adjustment or "raw").strip().lower() or "raw"
    raw = f"{symbol}|{start_s}|{end_s}|{timeframe}|{feed}|{adj}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_bars_cache_table(conn, db_name: str, table_name: str) -> None:
    if _store is None or conn is None:
        return
    key = f"{db_name}:{table_name}"
    if _table_ensured.get(key):
        return
    try:
        _db_schema.ensure_table(table_name)           # R25
        _table_ensured[key] = True
    except Exception:
        pass


def get_bars_chunk_cached(
    conn,
    symbol: str,
    chunk_start,
    chunk_end,
    timeframe: str,
    feed: str,
    fetch_fn,
    adjustment: str = "raw",
    db_name: str = DEFAULT_DB_NAME,
    table_name: str = ALPACA_BARS_CACHE_TABLE,
):
    """
    Return bars for one chunk: from cache if present, otherwise call fetch_fn() and store.

    Args:
        conn: truthy cache handle (or None to skip the cache).
        symbol: Ticker (e.g. "AAPL").
        chunk_start: Chunk start (datetime or date or YYYY-MM-DD str).
        chunk_end: Chunk end (datetime or date or YYYY-MM-DD str).
        timeframe: Alpaca timeframe (e.g. "1Day", "1Hour").
        feed: Alpaca feed (e.g. "sip", "iex").
        fetch_fn: Callable with no arguments that returns a list of bar dicts (t, o, h, l, c, v, ...).
        db_name: Database name.
        table_name: Table name.

    Returns:
        Tuple of (list of bar dicts for this chunk, from_cache: bool).
    """
    if conn is not None and _store is not None:
        _ensure_bars_cache_table(conn, db_name, table_name)
        cache_id = alpaca_bars_cache_key(symbol, chunk_start, chunk_end, timeframe, feed, adjustment)
        try:
            doc = _store.get(table_name, cache_id)
            if doc and "bars" in doc:
                raw = doc.get("bars")
                if raw:
                    if doc.get("compressed"):
                        raw = gzip.decompress(base64.b64decode(raw)).decode("utf-8")
                    bars = json.loads(raw)
                    if (isinstance(bars, list) and bars
                            and _cache_chunk_is_complete(
                                bars, chunk_start, chunk_end)):
                        return (bars, True)
                    # Coverage failed, or an earlier credential/network failure
                    # cached ``[]`` as if it proved there were no market bars.
                    # Empty history is not authoritative: refetch it.
        except Exception:
            pass
    bars = fetch_fn()
    if conn is not None and _store is not None and isinstance(bars, list):
        try:
            cache_id = alpaca_bars_cache_key(symbol, chunk_start, chunk_end, timeframe, feed, adjustment)
            bars_json = json.dumps(bars)
            payload = bars_json
            compressed = False
            if len(bars_json) > _BARS_COMPRESS_THRESHOLD:
                payload = base64.b64encode(gzip.compress(bars_json.encode("utf-8"))).decode("ascii")
                compressed = True
            # Skip cache writes when coverage looks bad or the response is
            # empty. The fetch layer cannot distinguish a legitimate gap from
            # missing credentials, a 401, or another transient failure, so an
            # empty response must remain retryable rather than poisoning every
            # later backtest that shares this cache key.
            should_persist = bool(bars) and _cache_chunk_is_complete(
                bars, chunk_start, chunk_end)
            if should_persist:
                _store.insert(table_name, {
                    "id": cache_id,
                    "symbol": symbol.upper(),
                    "start_date": _to_date_str(chunk_start),
                    "end_date": _to_date_str(chunk_end),
                    "timeframe": timeframe,
                    "feed": feed,
                    "bars": payload,
                    "compressed": compressed,
                    "cached_at": datetime.utcnow().isoformat() + "Z",
                }, conflict="replace")
        except Exception:
            pass
    return (bars if isinstance(bars, list) else [], False)


def _cache_chunk_is_complete(bars, chunk_start, chunk_end) -> bool:
    """Return True when the cached/fetched bars cover (close enough to) the
    requested chunk window.

    A chunk is "complete" when the last bar's timestamp is within
    `_CACHE_COVERAGE_TOLERANCE_DAYS` of the requested chunk_end (or when
    chunk_end is in the future relative to today, in which case "today" is
    the de-facto end). This catches the common bug where a previous fetch
    ran while chunk_end was still in the future and ended up locking in a
    truncated bar range.

    Returns True for empty bar lists — caller decides separately whether
    to persist those (they're sometimes legitimate, e.g. delisted symbols).
    """
    if not isinstance(bars, list) or not bars:
        return True
    end_dt = _coerce_to_datetime(chunk_end)
    if end_dt is None:
        return True
    last_t = bars[-1].get("t") if isinstance(bars[-1], dict) else None
    last_dt = _coerce_to_datetime(last_t)
    if last_dt is None:
        return True
    # If the requested chunk_end is in the future, the de-facto end is now.
    today = datetime.utcnow()
    effective_end = end_dt if end_dt <= today else today
    gap = effective_end - last_dt
    return gap <= timedelta(days=_CACHE_COVERAGE_TOLERANCE_DAYS)


def _coerce_to_datetime(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    if not s:
        return None
    if "T" in s:
        s = s.replace("Z", "")
        try:
            return datetime.fromisoformat(s).replace(tzinfo=None)
        except ValueError:
            return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None
