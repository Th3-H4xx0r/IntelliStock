"""ALFRED (FRED vintage) macro backfill into TrendMacroPIT (plan Task 3).

Endpoint (recon 2026-11-04, re-checked 2026-11-05): the keyless
``alfred.stlouisfed.org/graph/alfredgraph.csv``. ``id`` and ``vintage_date``
take comma-separated lists, one CSV column per vintage date, each column
holding the values in effect on that day (a release on day D is in D's
column). ALFRED silently drops columns past the 12th, so a call asks for
``VINTAGES_PER_CALL`` = 12 days and the header is checked. The FRED JSON API
(``realtime_start``/``realtime_end``) would return the same vintages in one
call but needs a key; this path needs none.

So every calendar day is one snapshot, and a row is written only when a
value differs from the previous day's snapshot -- the first release, then
each revision: ``id = "series|obs_date|vintage_date"``. Availability (ruling
R13): release series at the vintage date 08:30 ET, the US release time;
the series FRED posts in the afternoon (``AFTERNOON_SERIES``: the daily
market series -- yields, spread, OAS, oil, dollar -- and FEDFUNDS, whose
monthly average comes with the afternoon H.15 release, ruling R30) at
18:00 ET. Stored as UTC.

Chunks and resume (ruling R14): days are grouped into fixed 12-day chunks
aligned to ``CHUNK_ANCHOR``, so a chunk is the same whatever range a run was
asked for. A run fetches every chunk overlapping ``start..end`` that has no
completion marker in TrendIngestProgress (``alfred|SERIES|chunk_start``), and
marks a chunk only once all of its days are fetched (a chunk reaching past
today is refetched next run, and meanwhile carries
``alfred_partial|SERIES|chunk_start`` with the last day it fetched --
``missing_coverage`` reads both kinds of marker). A chunk is seeded with the values stored
before it when the previous chunk is marked; otherwise its first day cannot
be dated, and values already present that day get ``vintage_date`` = that day
(a late stamp, never an early one) and ``vintage_exact = False``.
``put_rows`` ignores rows it already holds.

CLI (run from ``backend/``)::

    python3 -m trend_research.ingest_macro --start 2020-07-01 --end 2026-11-04
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import math
import re
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from trend_research.pit_store import done_keys, mark_done, put_rows, read_available

__all__ = ["backfill_macro", "macro_as_of", "missing_coverage", "MACRO_SERIES", "main"]

log = logging.getLogger(__name__)

ALFRED_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
TABLE = "TrendMacroPIT"
SOURCE = "alfred"
ET = ZoneInfo("America/New_York")
RELEASE_TIME_ET = dtime(8, 30)
MARKET_TIME_ET = dtime(18, 0)
PROGRESS_SOURCE = "alfred"
PARTIAL_SOURCE = "alfred_partial"
CHUNK_ANCHOR = date(2000, 1, 1)

MACRO_SERIES = (
    "CPIAUCSL",       # CPI, all items
    "CPILFESL",       # core CPI
    "PCEPILFE",       # core PCE price index
    "UNRATE",         # unemployment rate
    "PAYEMS",         # nonfarm payrolls
    "ICSA",           # initial jobless claims (weekly)
    "RSAFS",          # retail sales
    "GDPC1",          # real GDP (quarterly)
    "FEDFUNDS",       # effective fed funds (monthly)
    "DGS10",          # 10-year Treasury
    "DGS2",           # 2-year Treasury
    "T10Y2Y",         # 10y-2y spread
    "BAMLH0A0HYM2",   # high-yield OAS
    "DCOILWTICO",     # WTI crude
    "DTWEXBGS",       # broad trade-weighted dollar
)

# Posted by FRED in the afternoon of their vintage day (ruling R13).
DAILY_MARKET_SERIES = frozenset({"DGS10", "DGS2", "T10Y2Y", "BAMLH0A0HYM2",
                                 "DCOILWTICO", "DTWEXBGS"})
#: Stamped 18:00 ET: the daily market series, and FEDFUNDS (the monthly
#: average is posted with the H.15 release in the afternoon, ruling R30).
AFTERNOON_SERIES = DAILY_MARKET_SERIES | {"FEDFUNDS"}

VINTAGES_PER_CALL = 12           # ALFRED drops columns past the 12th
OBS_LOOKBACK_DAYS = 3 * 365      # revisions older than this are not tracked
MIN_REQUEST_INTERVAL = 1.0
MAX_ATTEMPTS = 6
BACKOFF_BASE = 2.0
BACKOFF_CAP = 60.0
REQUEST_TIMEOUT = 60
# ALFRED's edge silently drops a bare "IntelliStock research" User-Agent
# (every request read-times-out); a product/version token is served.
HEADERS = {"User-Agent": "IntelliStock/1.0 (research; +https://github.com/Th3-H4xx0r/IntelliStock)"}

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_SERIES_RE = re.compile(r"^[A-Z0-9_]+$")


def _check_series(series: str) -> None:
    if not isinstance(series, str) or not _SERIES_RE.match(series):
        raise ValueError("not a FRED series id: %r" % (series,))


def _available_at(series: str, vintage: date) -> str:
    at = MARKET_TIME_ET if series in AFTERNOON_SERIES else RELEASE_TIME_ET
    local = datetime.combine(vintage, at, tzinfo=ET)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _series_range(series: str) -> tuple:
    # "|" is 0x7C and "}" 0x7D: every "SERIES|..." id sorts inside [lo, hi).
    return series + "|", series + "}"


# -- HTTP --------------------------------------------------------------------------

def _default_http_get(url, *, params, headers, timeout):
    import requests
    return requests.get(url, params=params, headers=headers, timeout=timeout)


class _Client:
    def __init__(self, http_get, sleep, clock):
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

    def get(self, params: dict, what: str) -> str:
        problem = ""
        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            self.requests += 1
            try:
                resp = self._http_get(ALFRED_URL, params=params, headers=HEADERS,
                                      timeout=REQUEST_TIMEOUT)
            except OSError as exc:
                problem = "network error %s" % type(exc).__name__
            else:
                status = getattr(resp, "status_code", None)
                text = getattr(resp, "text", "")
                text = text if isinstance(text, str) else ""
                if status == 200:
                    return text
                if status not in _RETRY_STATUSES:
                    raise RuntimeError("alfred HTTP %s (%s): %s"
                                       % (status, what, text.strip()[:200]))
                problem = "HTTP %s" % status
            if attempt + 1 == MAX_ATTEMPTS:
                break
            delay = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
            log.warning("alfred %s (%s); retry %d/%d in %.0fs", problem, what,
                        attempt + 1, MAX_ATTEMPTS - 1, delay)
            self._sleep(delay)
        raise RuntimeError("alfred: gave up after %d attempts (%s, %s)"
                           % (MAX_ATTEMPTS, problem, what))


# -- parsing -----------------------------------------------------------------------

def _value(cell: str):
    cell = (cell or "").strip()
    if cell in ("", "."):
        return None
    try:
        v = float(cell)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _parse_snapshots(text: str, series: str, days: list) -> list:
    """``[(day, {obs_date: value})]`` in ``days`` order; RuntimeError unless
    the CSV has exactly one column per requested day."""
    what = "%s %s..%s" % (series, days[0], days[-1])
    body = text.lstrip("﻿").strip()
    if not body.startswith("observation_date"):
        raise RuntimeError("alfred %s: response is not the vintage CSV: %r"
                           % (what, body[:120]))
    reader = csv.reader(io.StringIO(body))
    header = next(reader)
    expected = ["observation_date"] + ["%s_%s" % (series, d.strftime("%Y%m%d"))
                                       for d in days]
    if [h.strip() for h in header] != expected:
        raise RuntimeError("alfred %s: expected %d vintage columns, got header %r"
                           % (what, len(days), header[:4] + (["..."] if len(header) > 4 else [])))
    snaps = [(d, {}) for d in days]
    for row in reader:
        if not row or not row[0].strip():
            continue
        obs = row[0].strip()
        try:
            date.fromisoformat(obs)
        except ValueError:
            continue
        for k, (_, snap) in enumerate(snaps):
            v = _value(row[k + 1]) if k + 1 < len(row) else None
            if v is not None:
                snap[obs] = v
    return snaps


# -- backfill ----------------------------------------------------------------------

def _store(store):
    # The real store gets the TR tables ensured first (pit_store.ensure_tables).
    from trend_research import pit_store
    return pit_store._store(store)


def _chunk_start(d: date) -> date:
    """First day of the fixed 12-day chunk holding ``d``."""
    return d - timedelta(days=(d - CHUNK_ANCHOR).days % VINTAGES_PER_CALL)


def _state_before(st, series: str, day: date) -> dict:
    """Per obs_date, the stored value of the latest vintage before ``day``."""
    lo, hi = _series_range(series)
    cut = day.isoformat()
    state, vintage_of = {}, {}
    for r in st.iter(st.between(TABLE, lo, hi)):
        v, obs = r.get("vintage_date", ""), r.get("obs_date")
        if obs and v < cut and v > vintage_of.get(obs, ""):
            state[obs], vintage_of[obs] = r.get("value"), v
    return state


def _backfill_series(client, st, series: str, start: date, end: date, today: date) -> int:
    done = done_keys(PROGRESS_SOURCE, series + "|", store=st)
    chunks = []
    c = _chunk_start(start)
    while c <= end and c <= today:
        chunks.append(c)
        c += timedelta(days=VINTAGES_PER_CALL)
    written = 0
    state = None            # values in effect the day before the next chunk, if known
    prev_fetched = None
    for c in chunks:
        key = "%s|%s" % (series, c.isoformat())
        if key in done:
            state, prev_fetched = None, None
            continue
        days = [c + timedelta(days=i) for i in range(VINTAGES_PER_CALL)
                if c + timedelta(days=i) <= today]
        prev = c - timedelta(days=VINTAGES_PER_CALL)
        if prev_fetched == prev and state is not None:
            seeded = True               # carried in memory from the chunk just fetched
        elif "%s|%s" % (series, prev.isoformat()) in done:
            state, seeded = _state_before(st, series, c), True
        else:
            state, seeded = {}, False
        cosd = (days[0] - timedelta(days=OBS_LOOKBACK_DAYS)).isoformat()
        n = len(days)
        text = client.get({"id": ",".join([series] * n),
                           "vintage_date": ",".join(d.isoformat() for d in days),
                           "cosd": ",".join([cosd] * n)},
                          "%s %s..%s" % (series, days[0], days[-1]))
        rows = []
        for snap_day, snap in _parse_snapshots(text, series, days):
            exact = seeded or snap_day != c
            vintage = snap_day.isoformat()
            for obs in sorted(snap):
                value = snap[obs]
                if state.get(obs) == value:
                    continue
                state[obs] = value
                rows.append({"id": "%s|%s|%s" % (series, obs, vintage),
                             "series": series, "obs_date": obs, "vintage_date": vintage,
                             "value": value, "vintage_exact": exact, "source": SOURCE,
                             "available_at": _available_at(series, snap_day)})
        n_written = put_rows(TABLE, rows, store=st) if rows else 0
        written += n_written
        prev_fetched = c
        if len(days) == VINTAGES_PER_CALL:
            mark_done(PROGRESS_SOURCE, key, {"first_day": days[0].isoformat(),
                                             "last_day": days[-1].isoformat(),
                                             "rows": n_written, "seeded": seeded},
                      store=st)
            done.add(key)
        else:
            mark_done(PARTIAL_SOURCE, key, {"first_day": days[0].isoformat(),
                                            "last_day": days[-1].isoformat(),
                                            "rows": n_written, "seeded": seeded},
                      store=st)
    log.info("alfred %s %s..%s: %d chunks, %d rows written", series, start, end,
             len(chunks), written)
    return written


def backfill_macro(start: date, end: date, *, series: tuple = MACRO_SERIES,
                   http_get=None, sleep=time.sleep, clock=time.monotonic, now=None,
                   store=None) -> int:
    """Record every first release and revision of ``series`` published on the
    days of each 12-day chunk overlapping ``start..end`` (inclusive; no day
    after today in New York) not yet marked complete; return rows written."""
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    series = tuple(series)
    for s in series:
        _check_series(s)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ET).date()
    if min(end, today) < start:
        return 0
    st = _store(store)
    client = _Client(http_get or _default_http_get, sleep, clock)
    total = 0
    for s in series:
        total += _backfill_series(client, st, s, start, end, today)
    log.info("alfred %s..%s: %d rows written in %d requests", start, end, total,
             client.requests)
    return total


def missing_coverage(first_day: date, last_day: date, *, series: tuple = MACRO_SERIES,
                     store=None) -> list:
    """``["SERIES chunk_start", ...]`` for every 12-day chunk overlapping
    ``first_day..last_day`` that was not fetched through ``min(chunk end,
    last_day)`` -- neither marked complete nor partially fetched that far."""
    st = _store(store)
    out = []
    for s_id in series:
        _check_series(s_id)
        full = done_keys(PROGRESS_SOURCE, s_id + "|", store=st)
        c = _chunk_start(first_day)
        while c <= last_day:
            key = "%s|%s" % (s_id, c.isoformat())
            need = min(c + timedelta(days=VINTAGES_PER_CALL - 1), last_day)
            if key not in full:
                row = st.get("TrendIngestProgress", "%s|%s" % (PARTIAL_SOURCE, key))
                got = str(((row or {}).get("meta") or {}).get("last_day") or "")
                if got < need.isoformat():
                    out.append("%s %s" % (s_id, c.isoformat()))
            c += timedelta(days=VINTAGES_PER_CALL)
    return out


def macro_as_of(series: str, decision_time: datetime, *, store=None) -> list:
    """Per observation date, the latest vintage of ``series`` with
    ``available_at < decision_time`` -- the first release until a revision
    is out, then the revision. Oldest observation first."""
    _check_series(series)
    lo, hi = _series_range(series)
    latest = {}
    for row in read_available(TABLE, lo_id=lo, hi_id=hi, decision_time=decision_time,
                              store=store):
        obs = row.get("obs_date")
        prior = latest.get(obs)
        if prior is None or row.get("vintage_date", "") > prior.get("vintage_date", ""):
            latest[obs] = row
    return [dict(latest[obs]) for obs in sorted(latest)]


# -- CLI -----------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m trend_research.ingest_macro",
        description="Backfill ALFRED macro vintages into TrendMacroPIT.")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--series", default=",".join(MACRO_SERIES),
                        help="comma-separated FRED series ids")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    series = tuple(s.strip() for s in args.series.split(",") if s.strip())
    total = backfill_macro(args.start, args.end, series=series)
    print("alfred backfill %s..%s: %d rows written" % (args.start, args.end, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
