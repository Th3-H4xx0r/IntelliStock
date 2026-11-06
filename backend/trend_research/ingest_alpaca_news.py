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
``content`` is capped at 4000 characters and ``summary`` at 1000 (ruling R9).

Resume (rulings R10b, R10c): no mid-day frontier is inferred, because
Alpaca's window and ``asc`` order may follow ``updated_at`` rather than
``created_at``. Each day is decided on its own, independent of any earlier
run's range: day ``d`` is skipped only when both ``d`` and ``d + 1`` hold
stored alpaca articles whose ``published_at`` AND ``available_at`` both fall
on that day -- only a fetch of that day's window can have produced such a
row, whichever field Alpaca filters on, so a run reached ``d + 1`` and ``d``
finished. The range's last day is always refetched. Anything fetched is
fetched whole from 00:00Z; ``put_rows`` ignores the duplicates.

Completion markers. A day fetched whole after it ended gets
``alpaca_news|YYYY-MM-DD`` in TrendIngestProgress -- so a day with no
articles is still known to be complete -- and a marked day is always
skipped. A day fetched while still in progress gets
``alpaca_news_partial|YYYY-MM-DD`` with the time it was fetched through
(meta ``through``). ``covered_through`` answers "was every article
available before T fetched?" from these markers; the ledger builder checks
it before a week (build_ledger's input-coverage gate).

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
from trend_research.pit_store import done_keys, mark_done, put_rows

__all__ = ["backfill_news", "resolve_paper_credentials", "normalize_article",
           "covered_through", "missing_days", "main", "StoreError"]

log = logging.getLogger(__name__)

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
TABLE = "TrendNewsPIT"
SOURCE = "alpaca"
PROGRESS_SOURCE = "alpaca_news"
PARTIAL_SOURCE = "alpaca_news_partial"
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
CONTENT_CAP = 4000               # characters (ruling R9)
SUMMARY_CAP = 1000

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
        "summary": _text(item.get("summary"))[:SUMMARY_CAP],
        "content": _text(item.get("content"))[:CONTENT_CAP],
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
                    try:
                        payload = resp.json()
                    except ValueError:   # truncated/garbled body: retry it
                        problem = "HTTP 200 with malformed JSON"
                    else:
                        if not isinstance(payload, dict):
                            raise RuntimeError("alpaca news: response is not an object")
                        return payload
                    delay = None
                elif status not in _RETRY_STATUSES:
                    body = _text(getattr(resp, "text", ""))[:200]
                    raise RuntimeError("alpaca news HTTP %s (start=%s): %s"
                                       % (status, params.get("start"), body))
                else:
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

def _has_stored_day(st, d: date) -> bool:
    """Whether a stored alpaca article proves day ``d``'s window was fetched:
    its ``published_at`` and its ``available_at`` (the id prefix) are both
    on ``d``.

    A row with only one of them on ``d`` is an edit spill -- created one day,
    edited on another -- and may have come from the other day's window if
    Alpaca filters on ``updated_at``. It is not evidence for either day. A
    day whose articles were all edited into the next day is simply refetched.
    """
    day_lo = _day_start(d)
    day_hi = day_lo + timedelta(days=1)
    sel = st.between(TABLE, _iso(day_lo), _iso(day_hi))
    # Small batches: the first matching row ends the scan.
    for row in st.iter(sel, batch=50):
        if row.get("source") != SOURCE:
            continue
        published = _parse_utc(row.get("published_at"))
        available = _parse_utc(row.get("available_at"))
        if (published is not None and available is not None
                and day_lo <= published < day_hi and day_lo <= available < day_hi):
            return True
    return False


def _marked_days(st, start: date, end: date) -> set:
    """Days in ``start..end`` with a completion marker."""
    keys = done_keys(PROGRESS_SOURCE, "", store=st)
    out = set()
    for k in keys:
        try:
            d = date.fromisoformat(k)
        except ValueError:
            continue
        if start <= d <= end:
            out.add(d)
    return out


def _completed_days(st, start: date, end: date) -> set:
    """Days in ``start..end`` to skip: every marked day (see the module
    docstring), and -- for rows stored before markers existed -- ``d < end``
    where both ``d`` and ``d + 1`` hold an article that only their own window
    could have produced (see ``_has_stored_day``): days are fetched in order,
    so a run that reached ``d + 1`` finished ``d``.
    """
    stored = {}

    def has(d):
        if d not in stored:
            stored[d] = _has_stored_day(st, d)
        return stored[d]

    done = _marked_days(st, start, end)
    d = start
    while d < end:
        nxt = d + timedelta(days=1)
        if d not in done and has(d) and has(nxt):
            done.add(d)
        d = nxt
    return done


def missing_days(start: date, end: date, *, store=None) -> list:
    """Days in ``start..end`` with no completion marker, oldest first."""
    st = _store(store)
    marked = _marked_days(st, start, end)
    out, d = [], start
    while d <= end:
        if d not in marked:
            out.append(d)
        d += timedelta(days=1)
    return out


def covered_through(t: datetime, *, store=None) -> bool:
    """Whether the day holding ``t`` (UTC) was fetched at least through
    ``t``: a completion marker, or a partial one whose ``through`` >= ``t``."""
    st = _store(store)
    d = t.astimezone(timezone.utc).date()
    if d in _marked_days(st, d, d):
        return True
    row = st.get("TrendIngestProgress", "%s|%s" % (PARTIAL_SOURCE, d.isoformat()))
    through = _parse_utc(((row or {}).get("meta") or {}).get("through"))
    return through is not None and through >= t


# -- backfill -----------------------------------------------------------------------

def _store(store):
    # The real store gets the TR tables ensured first (pit_store.ensure_tables).
    from trend_research import pit_store
    return pit_store._store(store)


def _backfill_day(client, st, d: date) -> tuple:
    day_lo = _day_start(d)
    day_hi = day_lo + timedelta(days=1)
    params = {"start": _iso(day_lo), "end": _iso(day_hi), "sort": "asc",
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
    if fetched and skipped * 2 > fetched:
        log.warning("alpaca news %s: %d of %d articles skipped as unparseable",
                    d, skipped, fetched)
    return fetched, written, skipped, pages


def backfill_news(start: date, end: date, *, key: str, secret: str, http_get=None,
                  sleep=time.sleep, clock=time.monotonic, store=None, now=None,
                  on_day=None) -> int:
    """Mirror every Alpaca news article of the UTC days ``start..end``
    (inclusive) into TrendNewsPIT; return how many rows were written.

    ``on_day(day, rows_written_so_far, skipped)`` is called after each day,
    fetched or skipped as complete (a job's progress and stop check; an
    exception it raises ends the run)."""
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    if not key or not secret:
        raise ValueError("alpaca data key and secret are required")
    st = _store(store)
    now = now or (lambda: datetime.now(timezone.utc))
    client = _Client(key, secret, http_get or _default_http_get, sleep, clock)
    total = 0
    done = _completed_days(st, start, end)
    marked = _marked_days(st, start, end)
    if done:
        log.info("alpaca news %s..%s: %d days already complete, skipped",
                 start, end, len(done))
    d = start
    while d <= end:
        if d in done:
            if d not in marked:
                mark_done(PROGRESS_SOURCE, d.isoformat(), {"inferred": True}, store=st)
            if on_day is not None:
                on_day(d, total, True)
            d += timedelta(days=1)
            continue
        started = now()
        fetched, written, skipped, pages = _backfill_day(client, st, d)
        total += written
        meta = {"fetched": fetched, "written": written, "pages": pages}
        if _day_start(d) + timedelta(days=1) <= started:
            mark_done(PROGRESS_SOURCE, d.isoformat(), meta, store=st)
        else:                       # the day was still running when fetched
            mark_done(PARTIAL_SOURCE, d.isoformat(), dict(meta, through=_iso(started)),
                      store=st)
        log.info("alpaca news %s: %d pages, %d fetched, %d written, %d skipped",
                 d, pages, fetched, written, skipped)
        if on_day is not None:
            on_day(d, total, False)
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
