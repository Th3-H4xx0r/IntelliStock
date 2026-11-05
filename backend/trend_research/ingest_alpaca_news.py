"""Alpaca (Benzinga) news backfill into TrendNewsPIT (spec §7.1, plan Task 2).

Each UTC day is paged in full -- ``sort=asc``, 50 per call, no symbol filter,
``include_content=true`` -- until Alpaca stops returning a
``next_page_token``. (The repo's older fetchers take one ``desc`` page and so
keep only the newest 50 articles before ``end``; this one keeps the whole day.)

Availability (ruling R5): ``available_at`` is ``updated_at`` when it is later
than ``created_at``, else ``created_at`` -- the content fetched is the latest
edit, so it was not visible before that edit. ``published_at`` stays
``created_at``. Stamps are second-resolution; ``available_at`` rounds a
fraction UP and ``published_at`` rounds it down, so rounding can only delay
availability, never advance it. An article whose availability cannot be
proven (no id, missing or naive or unparseable stamps) is skipped.

Resume: a re-run starts each day at the newest ``published_at`` already
stored for that day (inclusive, because a page boundary can split a second;
``put_rows`` ignores the re-fetched duplicates).

Credentials: only a paper brokerage's data keys (instance
``alpaca-paper-fwd``). The keys go in request headers and nowhere else --
never in logs, errors or output.

CLI (run from ``backend/``)::

    python3 -m trend_research.ingest_alpaca_news --start 2025-01-01 --end 2026-11-05
"""
from __future__ import annotations

import argparse
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone

from db.errors import StoreError
from stock_credential_boundary import (
    linked_alpaca_brokerage_id,
    resolve_linked_alpaca_credentials,
)
from trend_research.pit_store import put_rows

__all__ = ["backfill_news", "resolve_paper_credentials", "normalize_article",
           "main", "StoreError"]

log = logging.getLogger(__name__)

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
TABLE = "TrendNewsPIT"
SOURCE = "alpaca"
PAPER_INSTANCE = "alpaca-paper-fwd"
LIVE_INSTANCES = frozenset({"alpaca-main"})

PAGE_LIMIT = 50                  # Alpaca's maximum; 200 is an HTTP 400
MAX_REQUESTS_PER_MINUTE = 200
# A little under the 200/min limit so clock jitter can never cross it.
MIN_REQUEST_INTERVAL = 60.0 / 190
MAX_ATTEMPTS = 8
BACKOFF_BASE = 2.0
BACKOFF_CAP = 60.0
REQUEST_TIMEOUT = 30
MAX_PAGES_PER_DAY = 1000         # busiest measured day was 25 pages
# How far past its day the resume scan looks for an article whose edit moved
# its id (= available_at) forward. Edits measured at <= 22.5 h; one beyond
# this window only makes the resume point earlier, i.e. a harmless re-fetch.
RESUME_EDIT_LAG = timedelta(days=1)

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


# -- time helpers ---------------------------------------------------------------

def _parse_utc(value) -> datetime | None:
    """A tz-aware UTC datetime, or None if ``value`` is not a zoned ISO stamp."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_ceil(dt: datetime) -> str:
    if dt.microsecond:
        dt = dt.replace(microsecond=0) + timedelta(seconds=1)
    return _iso(dt)


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


# -- normalisation ----------------------------------------------------------------

def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _symbols(value) -> list:
    out = []
    for s in value or ():
        if isinstance(s, str):
            sym = s.strip().upper()
            if sym and sym not in out:
                out.append(sym)
    return out


def normalize_article(item) -> dict | None:
    """One Alpaca news item as a TrendNewsPIT row, or None if its
    availability cannot be proven."""
    if not isinstance(item, dict):
        return None
    raw_id = item.get("id")
    source_id = str(raw_id).strip() if raw_id is not None else ""
    if not source_id or "|" in source_id:
        return None
    created = _parse_utc(item.get("created_at"))
    if created is None:
        return None
    available = created
    raw_updated = item.get("updated_at")
    if raw_updated not in (None, ""):
        updated = _parse_utc(raw_updated)
        if updated is None:
            return None            # an edit we cannot date: unprovable availability
        if updated > created:
            available = updated
    available_at = _iso_ceil(available)
    return {
        "id": "%s|%s|%s" % (available_at, SOURCE, source_id),
        "source": SOURCE,
        "source_id": source_id,
        "published_at": _iso(created.replace(microsecond=0)),
        "available_at": available_at,
        "headline": _text(item.get("headline")),
        "summary": _text(item.get("summary")),
        "content": _text(item.get("content")),
        "symbols": _symbols(item.get("symbols")),
        "url": _text(item.get("url")),
        "author": _text(item.get("author")),
    }


# -- HTTP -------------------------------------------------------------------------

def _default_http_get(url, *, params, headers, timeout):
    import requests
    return requests.get(url, params=params, headers=headers, timeout=timeout)


class _Client:
    """Paced (<= 200 req/min) GETs with bounded retry on 429/5xx/network."""

    def __init__(self, key, secret, http_get, sleep, clock):
        self._headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self._http_get = http_get
        self._sleep = sleep
        self._clock = clock
        self._last = None
        self.requests = 0

    def _pace(self):
        if self._last is not None:
            wait = self._last + MIN_REQUEST_INTERVAL - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    @staticmethod
    def _retry_after(resp) -> float | None:
        headers = getattr(resp, "headers", None) or {}
        try:
            value = float(headers.get("Retry-After"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        return min(value, BACKOFF_CAP)

    def get(self, params: dict) -> dict:
        problem = ""
        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            self.requests += 1
            try:
                resp = self._http_get(NEWS_URL, params=params, headers=self._headers,
                                      timeout=REQUEST_TIMEOUT)
            except OSError as exc:      # requests' errors subclass IOError
                problem = "network error %s" % type(exc).__name__
                delay = None
            else:
                status = getattr(resp, "status_code", None)
                if status == 200:
                    payload = resp.json()
                    if not isinstance(payload, dict):
                        raise RuntimeError("alpaca news: response is not an object")
                    return payload
                if status not in _RETRY_STATUSES:
                    body = _text(getattr(resp, "text", ""))[:200]
                    raise RuntimeError("alpaca news HTTP %s (start=%s): %s"
                                       % (status, params.get("start"), body))
                problem = "HTTP %s" % status
                delay = self._retry_after(resp)
            if attempt + 1 == MAX_ATTEMPTS:
                break
            if delay is None:
                delay = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
            log.warning("alpaca news %s (start=%s); retry %d/%d in %.1fs", problem,
                        params.get("start"), attempt + 1, MAX_ATTEMPTS - 1, delay)
            self._sleep(delay)
        raise RuntimeError("alpaca news: gave up after %d attempts (%s, start=%s)"
                           % (MAX_ATTEMPTS, problem, params.get("start")))


# -- resume -------------------------------------------------------------------------

def _resume_point(st, day_lo: datetime, day_hi: datetime) -> str | None:
    """The newest stored alpaca ``published_at`` inside ``[day_lo, day_hi)``."""
    newest = None
    sel = st.between(TABLE, _iso(day_lo), _iso(day_hi + RESUME_EDIT_LAG))
    for row in st.iter(sel):
        if row.get("source") != SOURCE:
            continue
        published = _parse_utc(row.get("published_at"))
        if published is None or not (day_lo <= published < day_hi):
            continue
        if newest is None or published > newest:
            newest = published
    return _iso(newest) if newest is not None else None


# -- backfill -----------------------------------------------------------------------

def _store(store):
    if store is not None:
        return store
    from db import store as default_store
    return default_store


def _backfill_day(client, st, d: date) -> tuple:
    day_lo = _day_start(d)
    day_hi = day_lo + timedelta(days=1)
    start = _resume_point(st, day_lo, day_hi) or _iso(day_lo)
    params = {"start": start, "end": _iso(day_hi), "sort": "asc",
              "limit": PAGE_LIMIT, "include_content": "true"}
    seen_tokens = set()
    fetched = written = skipped = pages = 0
    while True:
        pages += 1
        if pages > MAX_PAGES_PER_DAY:
            raise RuntimeError("alpaca news %s: more than %d pages" % (d, MAX_PAGES_PER_DAY))
        payload = client.get(params)
        items = payload.get("news") or []
        rows = []
        for item in items:
            row = normalize_article(item)
            if row is None:
                skipped += 1
            else:
                rows.append(row)
        fetched += len(items)
        if rows:
            written += put_rows(TABLE, rows, store=st)
        token = payload.get("next_page_token")
        if not token:
            break
        if token in seen_tokens:
            raise RuntimeError("alpaca news %s: repeated page token" % (d,))
        seen_tokens.add(token)
        params = dict(params, page_token=token)
    return fetched, written, skipped, pages, start


def backfill_news(start: date, end: date, *, key: str, secret: str, http_get=None,
                  sleep=time.sleep, clock=time.monotonic, store=None) -> int:
    """Mirror every Alpaca news article of the UTC days ``start..end``
    (inclusive) into TrendNewsPIT; return how many rows were written."""
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    if not key or not secret:
        raise ValueError("alpaca data key and secret are required")
    st = _store(store)
    client = _Client(key, secret, http_get or _default_http_get, sleep, clock)
    total = 0
    d = start
    while d <= end:
        fetched, written, skipped, pages, from_ = _backfill_day(client, st, d)
        total += written
        log.info("alpaca news %s: from %s, %d pages, %d fetched, %d written, %d skipped",
                 d, from_, pages, fetched, written, skipped)
        d += timedelta(days=1)
    log.info("alpaca news %s..%s: %d rows written in %d requests",
             start, end, total, client.requests)
    return total


# -- credentials --------------------------------------------------------------------

def resolve_paper_credentials(instance_id: str = PAPER_INSTANCE, *, store=None,
                              resolver=resolve_linked_alpaca_credentials) -> tuple:
    """``(key, secret)`` of the instance's linked Alpaca DATA brokerage.

    Raises RuntimeError unless that brokerage is a paper account, both by its
    row's ``alpaca_paper`` flag and by the strict resolver's verdict.
    """
    if instance_id in LIVE_INSTANCES:
        raise RuntimeError("refusing %s: news ingest uses paper data credentials only"
                           % instance_id)
    st = _store(store)
    inst = st.get("Instances", instance_id)
    if not inst:
        raise RuntimeError("instance %s not found" % instance_id)
    brokerage_id = linked_alpaca_brokerage_id(inst, data=True)
    brokerage = st.get("BrokerageAccounts", brokerage_id)
    if not isinstance(brokerage, dict) or brokerage.get("alpaca_paper") is not True:
        raise RuntimeError("refusing %s: its data brokerage is not a paper account"
                           % instance_id)
    creds = resolver(inst, brokerage, data=True)
    if creds.paper is not True:
        raise RuntimeError("refusing %s: resolved credentials are not paper" % instance_id)
    return creds.key, creds.secret


# -- CLI ------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m trend_research.ingest_alpaca_news",
        description="Backfill Alpaca (Benzinga) news into TrendNewsPIT.")
    parser.add_argument("--start", required=True, type=date.fromisoformat,
                        help="first UTC day, YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=date.fromisoformat,
                        help="last UTC day (inclusive), YYYY-MM-DD")
    parser.add_argument("--instance", default=PAPER_INSTANCE,
                        help="paper instance whose data credentials to use")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    key, secret = resolve_paper_credentials(args.instance)
    total = backfill_news(args.start, args.end, key=key, secret=secret)
    print("alpaca news backfill %s..%s: %d rows written" % (args.start, args.end, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
