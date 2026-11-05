"""GDELT DOC 2.0 theme backfill into TrendThemesPIT and TrendNewsPIT (plan Task 3).

Two phases, both resumable:

1. Timelines. Per theme, in chunks of ``CHUNK_DAYS``: ``timelinevolraw``
   (matching-article count, plus ``norm`` = every article GDELT monitored)
   and ``timelinetone``. One TrendThemesPIT row per theme per UTC day,
   ``id = "YYYY-MM-DD|theme"``, available one hour after the day ends
   (``AVAILABILITY_LAG``). Points at sub-daily resolution are summed (volume)
   or volume-weighted (tone) into the day. A chunk whose every complete day is
   already stored is skipped.

2. Article titles (ruling R1). Per UTC day and theme, ``artlist`` sorted by
   relevance, at most ``ARTICLES_PER_THEME_DAY``. Each article is a
   TrendNewsPIT row, ``source = "gdelt"``, ``source_id =
   sha256(url)[:16]``, available at its ``seendate`` (GDELT's crawl time).
   A day is refetched for any theme with no gdelt row on it yet.

   Caveat for readers: GDELT ranks a day's articles over the whole day, so
   which morning titles made the top 25 depends on the afternoon. Each row
   carries ``selection_window_end``; a reader that must exclude even that
   effect uses titles only from windows ending before the decision.

A day is written only once complete: its availability (day end + 1 h) must
not be after ``now``. Rows are write-once (``put_rows``), so a partial day
written early would be frozen.

Rate limit: GDELT 429s at 6 s spacing and is bursty at 15-20 s (recon
2026-11-04), so requests start at least ``MIN_REQUEST_INTERVAL`` = 15 s
apart, and a 429 or the "limit requests" text backs off from 20 s.

CLI (run from ``backend/``)::

    python3 -m trend_research.ingest_gdelt --start 2020-07-01 --end 2026-11-04
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

from trend_research.pit_store import put_rows, read_available

__all__ = ["backfill_themes", "themes_between", "THEMES", "THEME_QUERIES",
           "normalize_article", "main"]

log = logging.getLogger(__name__)

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
THEMES_TABLE = "TrendThemesPIT"
NEWS_TABLE = "TrendNewsPIT"
SOURCE = "gdelt"

MIN_REQUEST_INTERVAL = 15.0
MAX_ATTEMPTS = 8
BACKOFF_BASE = 20.0
BACKOFF_CAP = 300.0
REQUEST_TIMEOUT = 60
CHUNK_DAYS = 365                 # a 1-year timeline came back daily (recon)
ARTICLES_PER_THEME_DAY = 25
ARTLIST_SORT = "hybridrel"
ARTLIST_FILTER = "sourcelang:english"   # titles the research model can read
AVAILABILITY_LAG = timedelta(hours=1)
TITLE_CAP = 300

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Theme slug -> GDELT DOC query. Slugs are TrendThemesPIT id parts (no "|").
# OR-lists sit in parentheses and phrases in quotes, as the DOC API requires;
# every keyword is >= 3 characters (shorter ones are rejected).
THEME_QUERIES = {
    # tech waves
    "ai_semiconductors": '("artificial intelligence" OR semiconductor OR semiconductors OR chipmaker)',
    "data_centers_power": '("data center" OR "data centers" OR "power grid" OR "electricity demand")',
    "energy_transition": '("renewable energy" OR "solar power" OR "wind power" OR "electric vehicles")',
    "crypto": "(bitcoin OR cryptocurrency OR crypto)",
    # geopolitics
    "war_conflict": "theme:ARMEDCONFLICT",
    "russia_ukraine": "(ukraine OR russia OR kremlin)",
    "middle_east": "(israel OR iran OR gaza OR houthi)",
    "tariffs": "(tariff OR tariffs)",
    "trade_dispute": "theme:ECON_TRADE_DISPUTE",
    "sanctions": '(sanctions OR "export controls")',
    "elections": "(election OR elections)",
    "china": "(china OR beijing OR chinese)",
    "defense": '("defense spending" OR "defence spending" OR pentagon OR "military aid")',
    # economy and rates
    "inflation": "theme:ECON_INFLATION",
    "central_banks": '("federal reserve" OR "central bank" OR "interest rates" OR "rate hike" OR "rate cut")',
    "bond_yields": '("treasury yields" OR "bond yields" OR "bond market")',
    "dollar": '("dollar index" OR "us dollar" OR "strong dollar" OR "weak dollar")',
    "recession": '(recession OR "economic slowdown" OR layoffs)',
    "labor_market": '("jobs report" OR unemployment OR payrolls)',
    "banking_stress": '("bank failure" OR "banking crisis" OR "bank run")',
    "oil_opec": '(opec OR "oil price" OR "oil prices" OR "crude oil")',
    "metals_mining": '(copper OR lithium OR "rare earths" OR "gold price")',
    "supply_chain": '("supply chain" OR shortages)',
    # consumer and health
    "consumer_spending": '("consumer spending" OR "retail sales" OR "consumer confidence")',
    "housing": '("housing market" OR "mortgage rates" OR "home prices")',
    "glp1_obesity": '("glp-1" OR ozempic OR wegovy OR mounjaro OR zepbound OR "obesity drug")',
    "pandemic": "(pandemic OR covid OR outbreak)",
}
THEMES = tuple(THEME_QUERIES)


# -- time helpers -------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _gdelt_dt(d: date, end_of_day: bool = False) -> str:
    return d.strftime("%Y%m%d") + ("235959" if end_of_day else "000000")


def _parse_gdelt_stamp(value) -> datetime | None:
    """``20220615T143000Z`` (or bare digits) as UTC; None if unparseable."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 8:
        digits += "000000"
    if len(digits) != 14:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _theme_available_at(d: date) -> datetime:
    return _day_start(d) + timedelta(days=1) + AVAILABILITY_LAG


# -- HTTP -----------------------------------------------------------------------------

def _default_http_get(url, *, params, headers, timeout):
    import requests
    return requests.get(url, params=params, headers=headers, timeout=timeout)


class _Client:
    """GETs started >= ``MIN_REQUEST_INTERVAL`` apart, with bounded retry on
    429/5xx, network errors, truncated JSON and GDELT's rate-limit text."""

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
        what = "%s %s %s" % (params.get("mode"), params.get("startdatetime"),
                             params.get("query"))
        problem = ""
        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            self.requests += 1
            delay = None
            try:
                resp = self._http_get(DOC_URL, params=params, headers={},
                                      timeout=REQUEST_TIMEOUT)
            except OSError as exc:      # requests' errors subclass IOError
                problem = "network error %s" % type(exc).__name__
            else:
                status = getattr(resp, "status_code", None)
                text = getattr(resp, "text", "")
                text = text.strip() if isinstance(text, str) else ""
                if status == 200:
                    if not text:
                        return {}
                    if text.startswith("{"):
                        try:
                            payload = json.loads(text, strict=False)
                        except ValueError:
                            problem = "HTTP 200 with malformed JSON"
                        else:
                            if not isinstance(payload, dict):
                                raise RuntimeError("gdelt %s: response is not an object"
                                                   % what)
                            return payload
                    elif "limit requests" in text.lower():
                        problem = "rate-limit text on HTTP 200"
                    else:
                        # GDELT answers a bad query with a plain-text 200.
                        raise RuntimeError("gdelt query rejected (%s): %s"
                                           % (what, text[:200]))
                elif status not in _RETRY_STATUSES:
                    raise RuntimeError("gdelt HTTP %s (%s): %s" % (status, what, text[:200]))
                else:
                    problem = "HTTP %s" % status
                    delay = self._retry_after(resp)
            if attempt + 1 == MAX_ATTEMPTS:
                break
            if delay is None:
                delay = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
            log.warning("gdelt %s (%s); retry %d/%d in %.0fs", problem, what,
                        attempt + 1, MAX_ATTEMPTS - 1, delay)
            self._sleep(delay)
        raise RuntimeError("gdelt: gave up after %d attempts (%s, %s)"
                           % (MAX_ATTEMPTS, problem, what))


# -- timelines --------------------------------------------------------------------------

def _timeline_points(payload: dict) -> list:
    """``[(utc datetime, point dict)]`` of the first series in a timeline."""
    timeline = payload.get("timeline") or []
    if not timeline or not isinstance(timeline[0], dict):
        return []
    out = []
    for point in timeline[0].get("data") or []:
        if not isinstance(point, dict):
            continue
        at = _parse_gdelt_stamp(point.get("date"))
        value = point.get("value")
        if at is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        out.append((at, point))
    return out


def _number(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _theme_rows(theme: str, days: list, vol_points: list, tone_points: list) -> list:
    """One row per day in ``days`` up to the last day the volume timeline
    reports; a gap before it is zero volume. Days after it are left out (GDELT
    may not have published them yet, and rows are write-once), and an empty
    timeline gives [] -- no data is not the same as zero volume."""
    if not vol_points:
        return []
    last_day = max(at for at, _ in vol_points).date()
    volume, total = {}, {}
    vol_at = {}
    for at, p in vol_points:
        d = at.date()
        volume[d] = volume.get(d, 0) + p["value"]
        norm = _number(p.get("norm"))
        if norm is not None:
            total[d] = total.get(d, 0) + norm
        vol_at[at] = p["value"]
    tone_sum, tone_w, tone_plain = {}, {}, {}
    for at, p in tone_points:
        d = at.date()
        w = vol_at.get(at)
        if w is not None and w > 0:
            tone_sum[d] = tone_sum.get(d, 0.0) + w * p["value"]
            tone_w[d] = tone_w.get(d, 0.0) + w
        tone_plain.setdefault(d, []).append(p["value"])
    rows = []
    for d in days:
        if d > last_day:
            break
        vol = int(round(volume.get(d, 0)))
        tot = total.get(d)
        tone = None
        if vol > 0:
            if tone_w.get(d):
                tone = tone_sum[d] / tone_w[d]
            elif tone_plain.get(d):
                tone = sum(tone_plain[d]) / len(tone_plain[d])
        rows.append({
            "id": "%s|%s" % (d.isoformat(), theme),
            "date": d.isoformat(),
            "theme": theme,
            "query": THEME_QUERIES[theme],
            "source": SOURCE,
            "volume": vol,
            "total_articles": int(round(tot)) if tot is not None else None,
            "share": (vol / tot) if tot else None,
            "tone": round(tone, 6) if tone is not None else None,
            "available_at": _iso(_theme_available_at(d)),
        })
    return rows


def _backfill_timelines(client, st, theme: str, days: list) -> int:
    written = 0
    for k in range(0, len(days), CHUNK_DAYS):
        chunk = days[k:k + CHUNK_DAYS]
        ids = ["%s|%s" % (d.isoformat(), theme) for d in chunk]
        if len(list(st.get_all(THEMES_TABLE, *ids))) == len(ids):
            continue
        base = {"query": THEME_QUERIES[theme], "format": "json",
                "startdatetime": _gdelt_dt(chunk[0]),
                "enddatetime": _gdelt_dt(chunk[-1], end_of_day=True)}
        lo, hi = _day_start(chunk[0]), _day_start(chunk[-1]) + timedelta(days=1)
        vol = [x for x in _timeline_points(client.get(dict(base, mode="timelinevolraw")))
               if lo <= x[0] < hi]
        tone = [x for x in _timeline_points(client.get(dict(base, mode="timelinetone")))
                if lo <= x[0] < hi]
        rows = _theme_rows(theme, chunk, vol, tone)
        if not rows:
            log.warning("gdelt %s %s..%s: empty volume timeline, nothing written",
                        theme, chunk[0], chunk[-1])
            continue
        n = put_rows(THEMES_TABLE, rows, store=st)
        written += n
        log.info("gdelt %s %s..%s: %d theme-days written", theme, chunk[0], chunk[-1], n)
    return written


# -- articles ---------------------------------------------------------------------------

def normalize_article(item, theme: str, window_end: datetime) -> dict | None:
    """One artlist item as a TrendNewsPIT row (ruling R1), or None without a
    url or a parseable ``seendate``."""
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    url = url.strip() if isinstance(url, str) else ""
    seen = _parse_gdelt_stamp(item.get("seendate"))
    if not url or seen is None:
        return None
    sid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    at = _iso(seen)

    def text(key, cap=200):
        v = item.get(key)
        return v.strip()[:cap] if isinstance(v, str) else ""

    return {
        "id": "%s|%s|%s" % (at, SOURCE, sid),
        "source": SOURCE,
        "source_id": sid,
        "published_at": at,
        "available_at": at,
        "headline": text("title", TITLE_CAP),
        "url": url,
        "domain": text("domain"),
        "language": text("language"),
        "sourcecountry": text("sourcecountry"),
        "theme": theme,
        "symbols": [],
        "selection_window_end": _iso(window_end),
    }


def _themes_with_articles(st, d: date) -> set:
    lo = _day_start(d)
    sel = st.between(NEWS_TABLE, _iso(lo), _iso(lo + timedelta(days=1)))
    return {row.get("theme") for row in st.iter(sel) if row.get("source") == SOURCE}


def _backfill_articles(client, st, themes: list, d: date) -> int:
    done = _themes_with_articles(st, d)
    window_end = _day_start(d) + timedelta(days=1)
    written = 0
    for theme in themes:
        if theme in done:
            continue
        payload = client.get({
            "query": "%s %s" % (THEME_QUERIES[theme], ARTLIST_FILTER),
            "mode": "artlist", "format": "json", "sort": ARTLIST_SORT,
            "maxrecords": ARTICLES_PER_THEME_DAY,
            "startdatetime": _gdelt_dt(d), "enddatetime": _gdelt_dt(d, end_of_day=True)})
        items = payload.get("articles") or []
        rows = [r for r in (normalize_article(i, theme, window_end)
                            for i in items[:ARTICLES_PER_THEME_DAY]) if r]
        if rows:
            written += put_rows(NEWS_TABLE, rows, store=st)
    return written


# -- backfill ---------------------------------------------------------------------------

def _store(store):
    if store is not None:
        return store
    from db import store as default_store
    return default_store


def backfill_themes(start: date, end: date, *, themes=THEMES, http_get=None,
                    sleep=time.sleep, clock=time.monotonic, now=None,
                    articles: bool = True, store=None) -> int:
    """Backfill theme-day timelines (and, with ``articles``, top titles) for
    the UTC days ``start..end`` inclusive; return rows written to both tables.
    Days not yet complete at ``now`` are left for a later run."""
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    themes = list(themes)
    unknown = [t for t in themes if t not in THEME_QUERIES]
    if unknown or not themes:
        raise ValueError("unknown GDELT themes: %r (known: %s)"
                         % (unknown, ", ".join(THEMES)))
    now = now or datetime.now(timezone.utc)
    days = []
    d = start
    while d <= end and _theme_available_at(d) <= now:
        days.append(d)
        d += timedelta(days=1)
    if not days:
        return 0
    st = _store(store)
    client = _Client(http_get or _default_http_get, sleep, clock)
    total = 0
    for theme in themes:
        total += _backfill_timelines(client, st, theme, days)
    if articles:
        for d in days:
            n = _backfill_articles(client, st, themes, d)
            total += n
            log.info("gdelt articles %s: %d written", d, n)
    log.info("gdelt %s..%s: %d rows written in %d requests", days[0], days[-1],
             total, client.requests)
    return total


def themes_between(start_day: date, end_day: date, decision_time: datetime, *,
                   store=None) -> list:
    """Theme-day rows dated ``start_day..end_day`` (inclusive) with
    ``available_at < decision_time``, by date then theme."""
    return read_available(THEMES_TABLE, lo_id=start_day.isoformat(),
                          hi_id=(end_day + timedelta(days=1)).isoformat(),
                          decision_time=decision_time, store=store)


# -- CLI ----------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m trend_research.ingest_gdelt",
        description="Backfill GDELT theme timelines and titles into the TR PIT store.")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--no-articles", action="store_true",
                        help="timelines only (skip the per-day artlist titles)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    total = backfill_themes(args.start, args.end, articles=not args.no_articles)
    print("gdelt backfill %s..%s: %d rows written" % (args.start, args.end, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
