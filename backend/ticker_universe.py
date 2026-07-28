"""Centralized US-equity ticker validator.

Problem (2026-04-22): LLM strategy paths (daily-sentiment classifier,
event-maintenance, trend extraction, macro-resolution) were returning
company names ("ADOBE") and hallucinated strings ("DLUX", "ABAC", "BGN",
"ACVF") as "tickers". Those strings flowed unchecked into the Nexus
discovered-stocks set, wasted Alpaca price-fetch quota and overlay-LLM
tokens, and polluted the GraphNexusDiscoveredStocks table until the
post-scoring quality filter evicted them cycles later.

Fix: a single validator used at every ingestion boundary.
  1. Regex `^[A-Z]{1,5}(\\.[A-Z])?$` — fast reject for non-ticker strings
     (company names like "ADOBE" are 5 chars but pass this, hence step 2).
  2. Membership in the cached Nasdaq/NYSE universe (refreshed once per
     process; 24h TTL). This is the ground-truth set — ADOBE is not in
     it because Adobe Inc. trades as ADBE.

The validator is pure / idempotent / thread-safe. It fails OPEN on
network errors (Nasdaq API down → treat all tickers as possibly-valid
so we never block legitimate trading; invalid strings will still fail
downstream at Alpaca price fetch with a clean error path).

Kept OUT of discover.py to avoid coupling the main backtest-discovery
entry point; all consumers import from here.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Iterable, Optional


# Accept both `.` (Nasdaq-style: BRK.B, BF.B) and `-` (Alpaca-style: BRK-B,
# BF-B) share-class suffixes. Previously only `.` was allowed, which silently
# rejected every legitimate Alpaca-keyed dash ticker at every ingestion site.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z])?$")

# Cached universe. Populated lazily on first call and refreshed after TTL.
_UNIVERSE: set[str] | None = None
_UNIVERSE_FETCHED_AT: float = 0.0
_UNIVERSE_TTL_SEC: float = 24 * 3600.0
_UNIVERSE_LOCK = threading.Lock()

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
NASDAQ_SCREENER_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "accept": "application/json",
    "referer": "https://www.nasdaq.com/",
}


def _fetch_universe() -> set[str]:
    """Pull the Nasdaq screener's full stock list. Best-effort; returns
    an empty set on any error so callers can fail open."""
    try:
        import requests
        r = requests.get(
            NASDAQ_SCREENER_URL,
            headers=NASDAQ_SCREENER_HEADERS,
            params={"tableonly": "true", "limit": 10000, "offset": 0, "download": "true"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data") or {}
        rows = data.get("rows") or []
        symbols: set[str] = set()
        for row in rows:
            if isinstance(row, dict):
                sym = (row.get("symbol") or row.get("ticker") or "").strip().upper()
            elif isinstance(row, (list, tuple)) and row:
                sym = str(row[0] or "").strip().upper()
            else:
                continue
            if sym and sym not in ("N/A", ""):
                symbols.add(sym)
                # Also admit the dot→dash normalization Alpaca uses (BRK.B ↔ BRK-B).
                if "." in sym:
                    symbols.add(sym.replace(".", "-"))
        return symbols
    except Exception:
        return set()


_UNIVERSE_MIN_RETRY_SEC: float = 60.0  # Don't re-hit Nasdaq faster than this on failure.
_UNIVERSE_LAST_FAIL_AT: float = 0.0


def _get_universe() -> set[str]:
    """Return the cached ticker universe, refreshing if TTL expired.

    Double-checked locking: the 30s Nasdaq REST call runs OUTSIDE the lock
    so N concurrent first-cycle callers don't serialise on a single thread's
    network fetch. Last-writer-wins on the published dict is acceptable
    because `_fetch_universe` is idempotent.

    On fetch failure, we throttle subsequent retries via
    `_UNIVERSE_MIN_RETRY_SEC` to prevent a persistently-429ing Nasdaq from
    freezing every ticker validation in the cycle.
    """
    global _UNIVERSE, _UNIVERSE_FETCHED_AT, _UNIVERSE_LAST_FAIL_AT
    now = time.time()
    # Fast path: cached + fresh.
    with _UNIVERSE_LOCK:
        if _UNIVERSE is not None and (now - _UNIVERSE_FETCHED_AT) <= _UNIVERSE_TTL_SEC:
            return _UNIVERSE
        # Failure throttle — don't hammer a down API.
        if _UNIVERSE is not None and (now - _UNIVERSE_LAST_FAIL_AT) < _UNIVERSE_MIN_RETRY_SEC:
            return _UNIVERSE
    # Fetch OUTSIDE the lock so concurrent callers don't block on network.
    fresh = _fetch_universe()
    with _UNIVERSE_LOCK:
        if fresh:
            _UNIVERSE = fresh
            _UNIVERSE_FETCHED_AT = time.time()
        else:
            _UNIVERSE_LAST_FAIL_AT = time.time()
            if _UNIVERSE is None:
                # First-call failure — seed an empty set so callers see
                # "fail-open via regex" rather than NoneType errors.
                _UNIVERSE = set()
        return _UNIVERSE or set()


def load_universe_snapshot(*, context, store):
    """Load dated universe membership for a historical decision."""
    from point_in_time_data import load_snapshot_payload

    return load_snapshot_payload(store, dataset="universe", context=context)


def _snapshot_universe_rows(payload) -> list[dict]:
    if isinstance(payload, dict):
        raw_rows = list(payload.get("rows") or [])
        raw_rows.extend(payload.get("symbols") or [])
    else:
        raw_rows = payload
    if not isinstance(raw_rows, (list, tuple, set)):
        return []
    rows_by_symbol: dict[str, dict] = {}
    for raw in raw_rows:
        if isinstance(raw, dict):
            symbol = (
                raw.get("sym")
                or raw.get("symbol")
                or raw.get("ticker")
                or ""
            )
            row = dict(raw)
            row["sym"] = str(symbol or "").strip().upper().replace(".", "-")
        else:
            row = {"sym": str(raw or "").strip().upper().replace(".", "-")}
        if row["sym"]:
            existing = rows_by_symbol.get(row["sym"], {})
            rows_by_symbol[row["sym"]] = {**existing, **row}
    return list(rows_by_symbol.values())


def is_valid_us_ticker(
    ticker: str,
    *,
    strict: bool = True,
    context=None,
    snapshot_store=None,
) -> bool:
    """Return True if `ticker` looks like a real US-listed equity symbol.

    Validation:
      1. Regex gate: uppercase 1-5 letters, optional `.X` suffix (class share).
      2. (if strict) Membership in the Nasdaq screener universe — blocks
         company-name hallucinations (ADOBE, DLUX, ABAC, BGN, ACVF).

    strict=False: regex-only. Use when network access is unreliable and
    you'd rather not call `_fetch_universe`. The caller accepts that
    hallucinated 4-5-letter uppercase strings will slip through.

    Fails OPEN on network errors: if the universe couldn't be fetched at
    ALL, strict mode degrades to regex-only so we never block trading
    because of a Nasdaq outage.
    """
    if not ticker:
        return False
    t = str(ticker).strip().upper()
    if not t or not _TICKER_RE.match(t):
        return False
    if not strict:
        return True
    if context is not None and not context.uses_live_sources:
        snapshot = load_universe_snapshot(
            context=context,
            store=snapshot_store,
        )
        membership = {
            row["sym"]
            for row in _snapshot_universe_rows(snapshot)
            if _TICKER_RE.match(row["sym"])
        }
        return t.replace(".", "-") in membership
    universe = _get_universe()
    if not universe:
        # Fail open — Nasdaq API unreachable, don't block trading.
        return True
    return t in universe


# --- Breadth universe (market-wide momentum screen source, 2026-07-24) ---
# The Nasdaq screener `download=true` rows carry marketCap / lastsale / volume
# columns that _fetch_universe discards. get_breadth_universe keeps them and
# returns a liquidity-floored, dollar-volume-ranked symbol list — the candidate
# pool for the breadth momentum scanner (EVALUATION only; buys stay gated).
_BREADTH_CACHE: list[str] | None = None
_BREADTH_CACHE_KEY: tuple | None = None
_BREADTH_FETCHED_AT: float = 0.0
_BREADTH_LAST_FAIL_AT: float = 0.0
_BREADTH_META_CACHE: list[dict] = []


def snapshot_current_universe() -> dict:
    """Return an owned snapshot of current membership and breadth metadata."""

    with _UNIVERSE_LOCK:
        symbols = {
            str(symbol or "").strip().upper().replace(".", "-")
            for symbol in (_UNIVERSE or set())
            if str(symbol or "").strip()
        }
        rows = [dict(row) for row in (_BREADTH_META_CACHE or [])]
    for row in rows:
        symbol = str(row.get("sym") or "").strip().upper().replace(".", "-")
        if symbol:
            row["sym"] = symbol
            symbols.add(symbol)
    rows.sort(key=lambda row: str(row.get("sym") or ""))
    return {"symbols": sorted(symbols), "rows": rows}


def _parse_money(v) -> float:
    """'$1,234,567' / '1234567' / 1234567 -> float; junk -> 0.0."""
    try:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v or "").strip().replace("$", "").replace(",", "").replace("%", "")
        if not s or s in ("N/A", "--"):
            return 0.0
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _fetch_universe_meta() -> list[dict]:
    """Nasdaq screener rows WITH metadata: [{sym, price, volume, mcap}]. Best-
    effort; empty list on any error (caller falls back to the narrow universe)."""
    try:
        import requests
        r = requests.get(
            NASDAQ_SCREENER_URL, headers=NASDAQ_SCREENER_HEADERS,
            params={"tableonly": "true", "limit": 10000, "offset": 0, "download": "true"},
            timeout=30,
        )
        r.raise_for_status()
        rows = ((r.json().get("data") or {}).get("rows")) or []
        out: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = (row.get("symbol") or row.get("ticker") or "").strip().upper()
            if not sym or sym in ("N/A", ""):
                continue
            out.append({
                "sym": sym.replace(".", "-"),   # Alpaca dash form
                "price": _parse_money(row.get("lastsale")),
                "volume": _parse_money(row.get("volume")),
                "mcap": _parse_money(row.get("marketCap")),
            })
        return out
    except Exception:
        return []


def get_breadth_universe(
    min_mcap: float = 2e9,
    min_dollar_volume: float = 5e6,
    price_floor: float = 5.0,
    top_n: int = 500,
    *,
    context=None,
    snapshot_store=None,
) -> list[str]:
    """Liquidity-floored, dollar-volume-ranked US-equity symbols for the breadth
    scan. Cached (24h TTL, keyed by the floors). Fails open to [] so the caller
    keeps its existing (narrow) universe. Pure metadata — NOT price history, so
    no look-ahead in a backtest; the momentum signal is computed from as-of bars
    downstream. (Universe membership uses current listings — a mild survivorship
    proxy for the liquidity floor, acceptable for a coarse candidate gate.)"""
    global _BREADTH_CACHE, _BREADTH_CACHE_KEY, _BREADTH_FETCHED_AT, _BREADTH_LAST_FAIL_AT
    global _BREADTH_META_CACHE
    if context is not None and not context.uses_live_sources:
        snapshot = load_universe_snapshot(
            context=context,
            store=snapshot_store,
        )
        snapshot_rows = _snapshot_universe_rows(snapshot)
        has_metadata = any(
            any(
                field in row
                for field in ("price", "lastsale", "volume", "mcap", "marketCap")
            )
            for row in snapshot_rows
        )
        if not has_metadata:
            return [
                row["sym"]
                for row in snapshot_rows
                if _TICKER_RE.match(row["sym"])
            ][:max(0, int(top_n))]
        historical_ranked: list[tuple[float, str]] = []
        for row in snapshot_rows:
            symbol = row["sym"]
            price = _parse_money(row.get("price", row.get("lastsale")))
            volume = _parse_money(row.get("volume"))
            market_cap = _parse_money(row.get("mcap", row.get("marketCap")))
            dollar_volume = price * volume
            if (
                price >= price_floor
                and market_cap >= min_mcap
                and dollar_volume >= min_dollar_volume
                and _TICKER_RE.match(symbol)
            ):
                historical_ranked.append((dollar_volume, symbol))
        historical_ranked.sort(reverse=True)
        return [
            symbol
            for _dollar_volume, symbol in historical_ranked[:max(0, int(top_n))]
        ]
    key = (round(min_mcap), round(min_dollar_volume), round(price_floor, 2), int(top_n))
    now = time.time()
    with _UNIVERSE_LOCK:
        if (_BREADTH_CACHE is not None and _BREADTH_CACHE_KEY == key
                and (now - _BREADTH_FETCHED_AT) <= _UNIVERSE_TTL_SEC):
            return list(_BREADTH_CACHE)
        # Failure throttle: don't re-hit a down/429ing Nasdaq every bar (a 30s
        # timeout per call would stall each backtest bar / live tick).
        if (now - _BREADTH_LAST_FAIL_AT) < _UNIVERSE_MIN_RETRY_SEC:
            return list(_BREADTH_CACHE or [])
    meta = _fetch_universe_meta()
    if not meta:
        with _UNIVERSE_LOCK:
            _BREADTH_LAST_FAIL_AT = time.time()
        return list(_BREADTH_CACHE or [])
    with _UNIVERSE_LOCK:
        _BREADTH_META_CACHE = [dict(row) for row in meta]
    ranked = []
    for m in meta:
        if (m["price"] >= price_floor and m["mcap"] >= min_mcap
                and m["price"] * m["volume"] >= min_dollar_volume
                and _TICKER_RE.match(m["sym"])):
            ranked.append((m["price"] * m["volume"], m["sym"]))
    ranked.sort(reverse=True)
    result = [s for _dv, s in ranked[:max(0, int(top_n))]]
    if result:
        with _UNIVERSE_LOCK:
            _BREADTH_CACHE, _BREADTH_CACHE_KEY, _BREADTH_FETCHED_AT = result, key, time.time()
    return list(result)


# Crypto pairs use Alpaca's slash form (e.g. BTC/USD, SHIB/USD, ETH/BTC): a
# 2-10 char alphanumeric base and a 3-4 letter quote (USD/USDT/USDC/BTC).
_CRYPTO_PAIR_RE = re.compile(r"^[A-Z0-9]{2,10}/[A-Z]{3,4}$")


def is_valid_crypto_pair(symbol: str) -> bool:
    """Return True if `symbol` is a well-formed Alpaca crypto pair (slash form,
    e.g. 'BTC/USD'). Regex-only — no Nasdaq universe call. Crypto instances use
    this in place of `is_valid_us_ticker`, whose US-equity regex rejects the
    slash and whose Nasdaq membership check is meaningless for crypto."""
    if not symbol:
        return False
    return bool(_CRYPTO_PAIR_RE.match(str(symbol).strip().upper()))


def filter_valid_tickers(tickers: Iterable[str], *, strict: bool = True) -> list[str]:
    """Return only the entries in `tickers` that pass `is_valid_us_ticker`.

    Preserves input order, de-dupes case-insensitively, uppercases output.
    """
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers or []:
        if not t:
            continue
        norm = str(t).strip().upper()
        if norm in seen:
            continue
        if is_valid_us_ticker(norm, strict=strict):
            seen.add(norm)
            out.append(norm)
    return out


def invalid_tickers(tickers: Iterable[str], *, strict: bool = True) -> list[str]:
    """Return the entries in `tickers` that do NOT pass validation.

    Useful for diagnostic logging without changing the caller's flow —
    callers can see which specific strings were rejected.
    """
    rejected: list[str] = []
    for t in tickers or []:
        if not t:
            continue
        norm = str(t).strip().upper()
        if not is_valid_us_ticker(norm, strict=strict):
            rejected.append(norm)
    return rejected


def universe_size() -> Optional[int]:
    """Return current universe size, or None if not yet fetched."""
    return len(_UNIVERSE) if _UNIVERSE is not None else None
