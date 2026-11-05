"""GDELT DOC 2.0 theme backfill into TrendThemesPIT and TrendNewsPIT (plan Task 3).

Two phases, both resumable through TrendIngestProgress markers (ruling R14):

1. Timelines. Per theme and calendar year: ``timelinevolraw``
   (matching-article count, plus ``norm`` = every article GDELT monitored)
   and ``timelinetone``. One TrendThemesPIT row per theme per UTC day,
   ``id = "YYYY-MM-DD|theme"``, available one hour after the day ends
   (``AVAILABILITY_LAG``). Points at sub-daily resolution are summed (volume)
   or volume-weighted (tone) into the day; a day the timeline skips is zero
   volume. The whole year is fetched whatever part of it was asked for (one
   call either way, and a year always comes back at daily resolution).
   A year is *final* once it ended ``FINAL_LAG`` before ``now``: its days run
   to 31 December and it is marked done (``gdelt_timeline|theme|YYYY``). A
   year still in progress is written only through the last day the timeline
   reports -- GDELT may not have published later days yet, and rows are
   write-once -- and is refetched on the next run.

2. Titles (rulings R1, R12). Per theme and week: ``artlist`` sorted by
   relevance, at most ``ARTICLES_PER_THEME_WEEK``, over the 7 days ending at
   that week's decision time (``week_decision_time``: 09:00 ET on the week's
   first NYSE session). Each article is a TrendNewsPIT row, ``source =
   "gdelt"``, ``source_id = sha256(url)[:16]``, and is available at the
   window end -- not at its ``seendate`` -- because GDELT ranks the window as
   a whole, so the selection is only known once the window has closed. An
   article seen at or after the window end is dropped. A (theme, week) is
   marked done (``gdelt_titles|theme|monday``) even when it returned nothing,
   so empty weeks are not refetched. ``articles_start`` limits the titles to
   the weeks that matter (the honest window plus warm-up) while timelines
   cover the full range.

A day or window is fetched only once complete: its availability (day end +
1 h, or the window end) must not be after ``now``.

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

from zoneinfo import ZoneInfo

from trend_research.pit_store import done_keys, mark_done, put_rows, read_available

__all__ = ["backfill_themes", "themes_between", "week_decision_time", "THEMES",
           "THEME_QUERIES", "normalize_article", "main"]

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
ARTICLES_PER_THEME_WEEK = 75
TITLE_WINDOW = timedelta(days=7)
DECISION_TIME_ET = (9, 0)        # titles close before the 09:30 ET decision tick
ET = ZoneInfo("America/New_York")
FINAL_LAG = timedelta(days=2)
TIMELINE_PROGRESS = "gdelt_timeline"
TITLES_PROGRESS = "gdelt_titles"
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


def _theme_rows(theme: str, days: list, vol_points: list, tone_points: list, *,
                final: bool) -> list:
    """One row per day in ``days``; a day the timeline skips is zero volume.
    Unless ``final``, days after the last one the timeline reports are left
    out (GDELT may not have published them yet, and rows are write-once). An
    empty timeline gives [] -- no data is not the same as zero volume."""
    if not vol_points:
        return []
    last_day = days[-1] if final else max(at for at, _ in vol_points).date()
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


def _year_days(year: int, now: datetime) -> list:
    """The days of ``year`` complete by ``now``."""
    out, d = [], date(year, 1, 1)
    while d.year == year and _theme_available_at(d) <= now:
        out.append(d)
        d += timedelta(days=1)
    return out


def _backfill_timelines(client, st, theme: str, years: list, now: datetime) -> int:
    done = done_keys(TIMELINE_PROGRESS, theme + "|", store=st)
    written = 0
    for year in years:
        key = "%s|%d" % (theme, year)
        if key in done:
            continue
        days = _year_days(year, now)
        if not days:
            continue
        final = (days[-1] == date(year, 12, 31)
                 and _day_start(days[-1]) + timedelta(days=1) + FINAL_LAG <= now)
        base = {"query": THEME_QUERIES[theme], "format": "json",
                "startdatetime": _gdelt_dt(days[0]),
                "enddatetime": _gdelt_dt(days[-1], end_of_day=True)}
        lo, hi = _day_start(days[0]), _day_start(days[-1]) + timedelta(days=1)
        vol = [x for x in _timeline_points(client.get(dict(base, mode="timelinevolraw")))
               if lo <= x[0] < hi]
        tone = [x for x in _timeline_points(client.get(dict(base, mode="timelinetone")))
                if lo <= x[0] < hi]
        rows = _theme_rows(theme, days, vol, tone, final=final)
        if not rows:
            log.warning("gdelt %s %d: empty volume timeline, nothing written", theme, year)
            continue
        n = put_rows(THEMES_TABLE, rows, store=st)
        written += n
        if final:
            mark_done(TIMELINE_PROGRESS, key, {"days": len(rows), "written": n}, store=st)
        log.info("gdelt %s %d: %d theme-days written%s", theme, year, n,
                 " (final)" if final else "")
    return written


# -- articles ---------------------------------------------------------------------------

def week_decision_time(monday: date) -> datetime:
    """09:00 ET on the first NYSE session of the week starting ``monday``
    (the Monday itself unless it is a holiday), as UTC."""
    from swing_trader.clock import trading_days
    sessions = sorted(trading_days(monday, monday + timedelta(days=4)))
    first = sessions[0] if sessions else monday
    hh, mm = DECISION_TIME_ET
    return datetime(first.year, first.month, first.day, hh, mm,
                    tzinfo=ET).astimezone(timezone.utc)


def normalize_article(item, theme: str, window_start: datetime,
                      window_end: datetime) -> dict | None:
    """One artlist item as a TrendNewsPIT row (rulings R1, R12), available at
    ``window_end``; None without a url, or unless its ``seendate`` falls in
    ``[window_start, window_end)``."""
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    url = url.strip() if isinstance(url, str) else ""
    seen = _parse_gdelt_stamp(item.get("seendate"))
    if not url or seen is None or not (window_start <= seen < window_end):
        return None
    sid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    at = _iso(window_end)

    def text(key, cap=200):
        v = item.get(key)
        return v.strip()[:cap] if isinstance(v, str) else ""

    return {
        "id": "%s|%s|%s" % (at, SOURCE, sid),
        "source": SOURCE,
        "source_id": sid,
        "published_at": _iso(seen),
        "available_at": at,
        "headline": text("title", TITLE_CAP),
        "url": url,
        "domain": text("domain"),
        "language": text("language"),
        "sourcecountry": text("sourcecountry"),
        "theme": theme,
        "symbols": [],
        "window_start": _iso(window_start),
        "window_end": at,
    }


def _title_weeks(first_day: date, last_day: date, now: datetime) -> list:
    """``(monday, window_start, window_end)`` for every week whose title
    window overlaps the UTC days ``first_day..last_day`` and has closed."""
    lo, hi = _day_start(first_day), _day_start(last_day) + timedelta(days=1)
    out = []
    monday = first_day - timedelta(days=first_day.weekday())
    while True:
        end = week_decision_time(monday)
        start = end - TITLE_WINDOW
        if start >= hi or end > now:
            break
        if end > lo:
            out.append((monday, start, end))
        monday += timedelta(days=7)
    return out


def _backfill_titles(client, st, themes: list, weeks: list) -> int:
    written = 0
    for theme in themes:
        done = done_keys(TITLES_PROGRESS, theme + "|", store=st)
        for monday, w_start, w_end in weeks:
            key = "%s|%s" % (theme, monday.isoformat())
            if key in done:
                continue
            payload = client.get({
                "query": "%s %s" % (THEME_QUERIES[theme], ARTLIST_FILTER),
                "mode": "artlist", "format": "json", "sort": ARTLIST_SORT,
                "maxrecords": ARTICLES_PER_THEME_WEEK,
                "startdatetime": w_start.strftime("%Y%m%d%H%M%S"),
                "enddatetime": (w_end - timedelta(seconds=1)).strftime("%Y%m%d%H%M%S")})
            items = payload.get("articles") or []
            rows = [r for r in (normalize_article(i, theme, w_start, w_end)
                                for i in items[:ARTICLES_PER_THEME_WEEK]) if r]
            n = put_rows(NEWS_TABLE, rows, store=st) if rows else 0
            written += n
            mark_done(TITLES_PROGRESS, key, {"returned": len(items), "kept": len(rows),
                                             "written": n, "window_end": _iso(w_end)},
                      store=st)
            log.info("gdelt titles %s week %s: %d of %d kept", theme, monday,
                     len(rows), len(items))
    return written


# -- backfill ---------------------------------------------------------------------------

def _store(store):
    if store is not None:
        return store
    from db import store as default_store
    return default_store


def backfill_themes(start: date, end: date, *, themes=THEMES, http_get=None,
                    sleep=time.sleep, clock=time.monotonic, now=None,
                    articles: bool = True, articles_start: date | None = None,
                    store=None) -> int:
    """Backfill theme-day timelines for every calendar year touching
    ``start..end`` and, with ``articles``, weekly titles for the weeks whose
    window overlaps ``articles_start`` (default ``start``) ``..end``; return
    rows written to both tables. Anything not complete at ``now`` is left for
    a later run."""
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    if articles_start is not None and (not isinstance(articles_start, date)
                                       or articles_start > end):
        raise ValueError("articles_start must be a date on or before end")
    themes = list(themes)
    unknown = [t for t in themes if t not in THEME_QUERIES]
    if unknown or not themes:
        raise ValueError("unknown GDELT themes: %r (known: %s)"
                         % (unknown, ", ".join(THEMES)))
    now = now or datetime.now(timezone.utc)
    st = _store(store)
    client = _Client(http_get or _default_http_get, sleep, clock)
    total = 0
    years = list(range(start.year, end.year + 1))
    for theme in themes:
        total += _backfill_timelines(client, st, theme, years, now)
    if articles:
        weeks = _title_weeks(articles_start or start, end, now)
        total += _backfill_titles(client, st, themes, weeks)
    log.info("gdelt %s..%s: %d rows written in %d requests", start, end, total,
             client.requests)
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
                        help="timelines only (skip the weekly artlist titles)")
    parser.add_argument("--articles-start", type=date.fromisoformat, default=None,
                        help="first day of the weekly titles (default --start)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    total = backfill_themes(args.start, args.end, articles=not args.no_articles,
                            articles_start=args.articles_start)
    print("gdelt backfill %s..%s: %d rows written" % (args.start, args.end, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
