"""GDELT theme timelines and weekly titles from the raw GKG 2.1 files (ruling R35).

The DOC API (``ingest_gdelt``) refuses most requests; GDELT's raw Global
Knowledge Graph files have no rate limit. One file per 15 minutes
(``GKG_URL``), each ~1,600 web documents with themes, tone, named entities
and the page title. One chronological pass over a sample of those files
feeds both tables, with the same row contracts as the DOC-API path:

1. Timelines -> TrendThemesPIT, one row per theme per UTC day, ``id =
   "YYYY-MM-DD|theme"``. ``volume`` counts the day's sampled web documents
   matching the theme, ``total_articles`` every sampled web document that
   day, ``tone`` is the mean V2Tone of the matching ones. A row is available
   one hour after its day ends (``ingest_gdelt._theme_available_at``), and a
   day is aggregated only once that is not after ``now`` and every sampled
   file of the day has been tried. More than ``MAX_MISSING_FRACTION`` of
   the day's files missing stops the run (``RuntimeError``) without writing
   the day: a transient gap must not become permanent zero rows. A written
   day is marked ``gdelt_gkg_day|YYYY-MM-DD``.

2. Titles -> TrendNewsPIT, per theme and week, over the same windows as
   the DOC path (``ingest_gdelt._title_weeks``): previous week's decision
   time (exclusive) to this week's (inclusive). A file belongs to a window
   when its stamp does -- a document's DATE is its file's batch time. An
   eligible document has a title that is on-topic: for a ``theme:`` query
   the GKG theme token, for a keyword query a keyword hit in the TITLE
   itself. Documents are grouped by ``digest.normalize_headline``; groups
   rank by how many distinct sources carried the headline in the window,
   then by earliest DATE, then by url hash; the earliest document of each
   group represents it, and the top ``ARTICLES_PER_THEME_WEEK`` are kept.
   Every row is available at the window end -- the ranking is over the
   whole window, so it is known only once the window has closed. Every
   (theme, week) is marked ``gdelt_titles|theme|monday`` (the marker the
   ledger's coverage gate reads), even with nothing kept.

Sampling: every ``sample_every``-th stamp counted from 00:00 UTC of each day
(4 -> the :00 file of every hour). A file is downloaded only when its day
needs a timeline or its window needs titles, so a rerun downloads nothing
for what is already marked. HTTP 404 is a real GDELT gap (counted as
missing); network errors, 5xx and a corrupt zip are retried.

CLI (run from ``backend/``)::

    python3 -m trend_research.ingest_gkg --start 2020-07-01 --end 2026-11-04
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import logging
import math
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

from trend_research import ingest_gdelt
from trend_research.digest import normalize_headline
from trend_research.pit_store import PROGRESS_TABLE, done_keys, mark_done, put_rows

__all__ = ["backfill_gkg", "GKG_URL", "DAY_PROGRESS", "SAMPLE_EVERY_CHOICES", "main"]

log = logging.getLogger(__name__)

GKG_URL = "https://data.gdeltproject.org/gdeltv2/%s.gkg.csv.zip"
VIA = "gkg"
DAY_PROGRESS = "gdelt_gkg_day"
SAMPLE_EVERY_CHOICES = (1, 2, 4, 8)
STAMP_MINUTES = 15
STAMPS_PER_DAY = 24 * 60 // STAMP_MINUTES
REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 5
BACKOFF = (5, 10, 20, 40)          # seconds before attempts 2..5
MAX_MISSING_FRACTION = 0.10
PROGRESS_EVERY = 24                # files between progress callbacks
POOL_CAP = 20000                   # title groups kept per (theme, week)
POOL_SLACK = 5000                  # growth past the cap before a prune

GKG_COLUMNS = 27
COL_DATE, COL_COLLECTION, COL_SOURCE, COL_URL = 1, 2, 3, 4
COL_THEMES, COL_LOCATIONS, COL_PERSONS, COL_ORGS = 7, 9, 11, 13
COL_TONE, COL_EXTRAS = 15, 26
WEB_COLLECTION = "1"
_TITLE_OPEN, _TITLE_CLOSE = "<PAGE_TITLE>", "</PAGE_TITLE>"

_QUERY_TERM = re.compile(r'"([^"]+)"|([^\s()"]+)')


# -- theme matching -----------------------------------------------------------------

def _keyword_terms(query: str) -> list:
    """The quoted phrases and bare words of a DOC OR-list or single keyword."""
    terms = []
    for phrase, word in _QUERY_TERM.findall(query):
        term = phrase or word
        if term and term != "OR":
            terms.append(term)
    if not terms:
        raise ValueError("GDELT query has no keywords: %r" % (query,))
    return sorted(set(terms), key=len, reverse=True)


def _keyword_regex(query: str):
    """The query's terms as one case-insensitive, word-bounded regex."""
    alts = "|".join(re.escape(t) for t in _keyword_terms(query))
    return re.compile(r"\b(?:%s)\b" % alts, re.IGNORECASE)


class _Matcher:
    """Which themes a GKG row matches -- anywhere (timelines: title,
    organizations, persons, location names) and in its title (titles).

    A keyword theme's regex runs only on text whose casefold contains one of
    its terms (casefolded): a cheap necessary condition -- except for the
    Turkish dotted/dotless i, which the regex folds to "i" and casefold does
    not -- so the per-file cost is substring scans, not a regex per theme."""

    def __init__(self, themes):
        self.tokens = []            # (theme, GKG theme token)
        self.keywords = []          # (theme, regex, casefolded terms)
        for theme in themes:
            query = ingest_gdelt.THEME_QUERIES[theme]
            if query.startswith("theme:"):
                self.tokens.append((theme, query[len("theme:"):].strip()))
            else:
                self.keywords.append((theme, _keyword_regex(query),
                                      tuple(t.casefold() for t in _keyword_terms(query))))
        self.token_themes = frozenset(t for t, _ in self.tokens)

    def _token_hits(self, themes_field: str) -> list:
        if not self.tokens or not themes_field:
            return []
        present = {t.split(",", 1)[0] for t in themes_field.split(";") if t}
        return [theme for theme, token in self.tokens if token in present]

    @staticmethod
    def _keyword_hits(keywords, text: str) -> list:
        folded = text.casefold()
        return [theme for theme, rx, terms in keywords
                if any(t in folded for t in terms) and rx.search(text)]

    def anywhere(self, cols, title: str) -> list:
        hits = self._token_hits(cols[COL_THEMES])
        if self.keywords:
            locs = [e.split("#")[1] for e in cols[COL_LOCATIONS].split(";") if e.count("#")]
            hay = "\n".join((title, cols[COL_ORGS].replace(";", "\n"),
                             cols[COL_PERSONS].replace(";", "\n"), "\n".join(locs)))
            hits.extend(self._keyword_hits(self.keywords, hay))
        return hits

    def in_title(self, cols, title: str, anywhere=None) -> list:
        """Themes whose titles may carry this row; ``anywhere`` (the row's
        ``anywhere`` hits, when computed) narrows the keyword check."""
        if not title:
            return []
        if anywhere is not None:
            hits = [t for t in anywhere if t in self.token_themes]
            hit_kw = set(anywhere)
            kws = [k for k in self.keywords if k[0] in hit_kw]
        else:
            hits = self._token_hits(cols[COL_THEMES])
            kws = self.keywords
        if kws:
            hits.extend(self._keyword_hits(kws, title))
        return hits


# -- GKG parsing --------------------------------------------------------------------

def _title(extras: str) -> str:
    i = extras.find(_TITLE_OPEN)
    if i < 0:
        return ""
    i += len(_TITLE_OPEN)
    j = extras.find(_TITLE_CLOSE, i)
    raw = extras[i:] if j < 0 else extras[i:j]
    return " ".join(html.unescape(raw).split())


def _tone(field: str):
    try:
        value = float(field.split(",", 1)[0])
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _web_rows(text: str):
    """Split columns of each web-collection row with all 27 columns. Lines
    split on "\n" only (a title may hold other line separators), one at a
    time rather than as a list of the whole file."""
    pos, size = 0, len(text)
    while pos < size:
        nl = text.find("\n", pos)
        if nl < 0:
            nl = size
        line, pos = text[pos:nl], nl + 1
        if not line:
            continue
        cols = line.rstrip("\r").split("\t")
        if len(cols) >= GKG_COLUMNS and cols[COL_COLLECTION] == WEB_COLLECTION:
            yield cols


# -- HTTP -----------------------------------------------------------------------------

def _default_http_get(url, *, timeout):
    import requests
    return requests.get(url, timeout=timeout)


def _stamp_name(at: datetime) -> str:
    return at.strftime("%Y%m%d%H%M%S")


def _fetch(http_get, sleep, at: datetime):
    """The file's text, or None when GDELT has no file for ``at`` (404, or a
    zip still corrupt after ``MAX_ATTEMPTS``)."""
    url = GKG_URL % _stamp_name(at)
    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            delay = BACKOFF[attempt - 1]
            log.warning("gkg %s (%s); retry %d/%d in %ds", problem, url, attempt,
                        MAX_ATTEMPTS - 1, delay)
            sleep(delay)
        try:
            resp = http_get(url, timeout=REQUEST_TIMEOUT)
        except OSError as exc:          # requests' errors subclass IOError
            problem = "network error %s" % type(exc).__name__
            continue
        status = getattr(resp, "status_code", None)
        if status == 404:
            return None
        if isinstance(status, int) and 500 <= status < 600:
            problem = "HTTP %s" % status
            continue
        if status != 200:
            raise RuntimeError("gkg HTTP %s for %s" % (status, url))
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                names = zf.namelist()
                if not names:
                    raise zipfile.BadZipFile("empty archive")
                data = zf.read(names[0])
        except (zipfile.BadZipFile, EOFError, ValueError) as exc:
            problem = "corrupt zip (%s)" % exc
            if attempt + 1 == MAX_ATTEMPTS:
                log.warning("gkg %s still corrupt after %d attempts; counted missing",
                            url, MAX_ATTEMPTS)
                return None
            continue
        return data.decode("utf-8", errors="replace")
    raise RuntimeError("gkg: gave up after %d attempts (%s, %s)" % (MAX_ATTEMPTS, problem, url))


# -- accumulators -----------------------------------------------------------------------

_iso = ingest_gdelt._iso


def _parse_doc_date(value: str, fallback: datetime) -> datetime:
    if len(value) == 14 and value.isdigit():
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


class _Day:
    def __init__(self, d: date, themes):
        self.date = d
        self.files = self.missing = self.total = 0
        self.volume = dict.fromkeys(themes, 0)
        self.tone_sum = dict.fromkeys(themes, 0.0)
        self.tone_n = dict.fromkeys(themes, 0)

    def rows(self, sample_every: int) -> list:
        at = _iso(ingest_gdelt._theme_available_at(self.date))
        out = []
        for theme, vol in self.volume.items():
            n = self.tone_n[theme]
            tone = self.tone_sum[theme] / n if vol and n else None
            out.append({
                "id": "%s|%s" % (self.date.isoformat(), theme),
                "date": self.date.isoformat(),
                "theme": theme,
                "query": ingest_gdelt.THEME_QUERIES[theme],
                "source": ingest_gdelt.SOURCE,
                "volume": vol,
                "total_articles": self.total,
                "share": (vol / self.total) if self.total else None,
                "tone": round(tone, 6) if tone is not None else None,
                "available_at": at,
                "via": VIA,
                "sample_every": sample_every,
                "files": self.files,
                "files_missing": self.missing,
            })
        return out


class _Week:
    """One title window's pools: per theme, normalized headline -> group
    ``[doc DATE, url hash, url, title, source, {sources}]``."""

    def __init__(self, monday: date, w_start: datetime, w_end: datetime, themes):
        self.monday, self.start, self.end = monday, w_start, w_end
        self.themes = list(themes)
        self.files = self.missing = 0
        self.pools = {t: {} for t in self.themes}
        self.candidates = dict.fromkeys(self.themes, 0)

    def add(self, theme: str, cols, title: str, file_at: datetime, sha_cache: dict):
        pool = self.pools.get(theme)
        if pool is None:
            return
        key = normalize_headline(title)
        if not key:
            return
        url = cols[COL_URL].strip()
        if not url:
            return
        self.candidates[theme] += 1
        sha = sha_cache.get(url)
        if sha is None:
            sha = sha_cache[url] = hashlib.sha256(url.encode("utf-8")).hexdigest()
        at = _parse_doc_date(cols[COL_DATE], file_at)
        source = cols[COL_SOURCE].strip()
        group = pool.get(key)
        if group is None:
            pool[key] = [at, sha, url, title, source, {source}]
            if len(pool) > POOL_CAP + POOL_SLACK:
                self._prune(theme)
            return
        group[5].add(source)
        if (at, sha) < (group[0], group[1]):
            group[:5] = [at, sha, url, title, source]

    @staticmethod
    def _rank(group):
        return (-len(group[5]), group[0], group[1])

    def _prune(self, theme: str):
        pool = self.pools[theme]
        keep = sorted(pool.items(), key=lambda kv: self._rank(kv[1]))[:POOL_CAP]
        self.pools[theme] = dict(keep)

    def rows(self, theme: str) -> list:
        end_iso, start_iso = _iso(self.end), _iso(self.start)
        ranked = sorted(self.pools[theme].values(), key=self._rank)
        out = []
        for at, sha, url, title, source, sources in ranked[:ingest_gdelt.ARTICLES_PER_THEME_WEEK]:
            sid = sha[:16]
            out.append({
                "id": "%s|%s|%s" % (end_iso, ingest_gdelt.SOURCE, sid),
                "source": ingest_gdelt.SOURCE,
                "source_id": sid,
                "published_at": _iso(at),
                "available_at": end_iso,
                "headline": title[:ingest_gdelt.TITLE_CAP],
                "url": url,
                "domain": source[:200],
                "language": "English",
                "sourcecountry": "",
                "theme": theme,
                "symbols": [],
                "window_start": start_iso,
                "window_end": end_iso,
                "via": VIA,
                "prominence": len(sources),
            })
        return out


def _too_many_missing(missing: int, attempted: int) -> bool:
    return attempted > 0 and missing > MAX_MISSING_FRACTION * attempted


# -- resume markers --------------------------------------------------------------------

def _done_days(st, first: date, last: date, themes) -> set:
    """Days in ``first..last`` already marked for every theme in ``themes``."""
    lo = "%s|%s" % (DAY_PROGRESS, first.isoformat())
    hi = "%s|%s" % (DAY_PROGRESS, (last + timedelta(days=1)).isoformat())
    wanted = set(themes)
    out = set()
    for row in st.iter(st.between(PROGRESS_TABLE, lo, hi)):
        covered = (row.get("meta") or {}).get("themes")
        if isinstance(covered, list) and wanted <= set(covered):
            try:
                out.add(date.fromisoformat(row.get("key")))
            except (TypeError, ValueError):
                continue
    return out


# -- backfill ---------------------------------------------------------------------------

def _check_args(start, end, articles_start, sample_every, themes):
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    if articles_start is not None and (not isinstance(articles_start, date)
                                       or articles_start > end):
        raise ValueError("articles_start must be a date on or before end")
    if isinstance(sample_every, bool) or sample_every not in SAMPLE_EVERY_CHOICES:
        raise ValueError("sample_every must be one of %s, got %r"
                         % (SAMPLE_EVERY_CHOICES, sample_every))
    unknown = [t for t in themes if t not in ingest_gdelt.THEME_QUERIES]
    if unknown or not themes:
        raise ValueError("unknown GDELT themes: %r (known: %s)"
                         % (unknown, ", ".join(ingest_gdelt.THEMES)))


def backfill_gkg(start: date, end: date, *, articles_start: date | None = None,
                 sample_every: int = 4, themes=ingest_gdelt.THEMES, http_get=None,
                 sleep=time.sleep, now=None, store=None, progress=None) -> int:
    """Theme-day timelines for ``start..end`` and weekly titles for the weeks
    whose window overlaps ``articles_start`` (default ``start``) ``..end``,
    from every ``sample_every``-th GKG file; return rows written to both
    tables. Anything not complete at ``now`` is left for a later run.
    ``progress(phase=, done=, total=, current=, rows_written=)`` is called
    every ``PROGRESS_EVERY`` files and after the last."""
    themes = list(themes)
    _check_args(start, end, articles_start, sample_every, themes)
    now = now or datetime.now(timezone.utc)
    st = ingest_gdelt._store(store)
    http_get = http_get or _default_http_get
    matcher = _Matcher(themes)

    # timeline days still to aggregate
    done_days = _done_days(st, start, end, themes)
    need_days = set()
    d = start
    while d <= end:
        if d not in done_days and ingest_gdelt._theme_available_at(d) <= now:
            need_days.add(d)
        d += timedelta(days=1)

    # title windows still to rank (only their unmarked themes)
    weeks = []
    titled = done_keys(ingest_gdelt.TITLES_PROGRESS, "", store=st)
    for monday, w_start, w_end in ingest_gdelt._title_weeks(articles_start or start, end, now):
        todo = [t for t in themes if "%s|%s" % (t, monday.isoformat()) not in titled]
        if todo:
            weeks.append(_Week(monday, w_start, w_end, todo))

    # the sampled stamps, chronologically, covering every needed day and window
    lo = ingest_gdelt._day_start(min(start, articles_start or start))
    hi = ingest_gdelt._day_start(end) + timedelta(days=1)
    if weeks:
        lo = min(lo, ingest_gdelt._day_start(weeks[0].start.date()))
        hi = max(hi, weeks[-1].end + timedelta(seconds=1))
    step = timedelta(minutes=STAMP_MINUTES * sample_every)
    stamps = []
    t = lo
    wi = 0
    while t < hi:
        while wi < len(weeks) and weeks[wi].end < t:
            wi += 1
        in_week = wi < len(weeks) and weeks[wi].start < t <= weeks[wi].end
        if t.date() in need_days or in_week:
            stamps.append(t)
        t += step

    total = len(stamps)
    written = 0
    day = None
    wi = 0
    sha_cache: dict = {}

    def flush_day():
        nonlocal written, day
        if day is None:
            return
        cur, day = day, None
        attempted = cur.files + cur.missing
        if attempted != STAMPS_PER_DAY // sample_every:
            raise RuntimeError("gkg day %s: %d of %d sampled files attempted"
                               % (cur.date, attempted, STAMPS_PER_DAY // sample_every))
        if _too_many_missing(cur.missing, attempted):
            raise RuntimeError("gkg day %s: %d of %d sampled files missing (> %d%%); "
                               "not written" % (cur.date, cur.missing, attempted,
                                                round(MAX_MISSING_FRACTION * 100)))
        n = put_rows(ingest_gdelt.THEMES_TABLE, cur.rows(sample_every), store=st)
        written += n
        mark_done(DAY_PROGRESS, cur.date.isoformat(),
                  {"via": VIA, "sample_every": sample_every, "files": cur.files,
                   "files_missing": cur.missing, "total_articles": cur.total,
                   "themes": themes, "written": n}, store=st)
        log.info("gkg day %s: %d docs in %d files (%d missing), %d theme-days written",
                 cur.date, cur.total, cur.files, cur.missing, n)

    def flush_week(week):
        nonlocal written
        attempted = week.files + week.missing
        if _too_many_missing(week.missing, attempted):
            raise RuntimeError("gkg title week %s: %d of %d sampled files missing (> %d%%); "
                               "no markers written" % (week.monday, week.missing, attempted,
                                                      round(MAX_MISSING_FRACTION * 100)))
        for theme in week.themes:
            rows = week.rows(theme)
            n = put_rows(ingest_gdelt.NEWS_TABLE, rows, store=st) if rows else 0
            written += n
            mark_done(ingest_gdelt.TITLES_PROGRESS, "%s|%s" % (theme, week.monday.isoformat()),
                      {"via": VIA, "files": week.files, "files_missing": week.missing,
                       "candidates": week.candidates[theme], "kept": len(rows),
                       "window_end": _iso(week.end)}, store=st)
        log.info("gkg titles week %s: %d themes ranked over %d files (%d missing)",
                 week.monday, len(week.themes), week.files, week.missing)
        week.pools = {}

    for i, at in enumerate(stamps, 1):
        if day is not None and day.date != at.date():
            flush_day()
        while wi < len(weeks) and weeks[wi].end < at:
            flush_week(weeks[wi])
            wi += 1
        if at.date() in need_days and day is None:
            day = _Day(at.date(), themes)
        week = weeks[wi] if (wi < len(weeks) and weeks[wi].start < at <= weeks[wi].end) else None
        cur_day = day if (day is not None and day.date == at.date()) else None

        text = _fetch(http_get, sleep, at)
        if text is None:
            log.warning("gkg %s: no file (counted missing)", _stamp_name(at))
            for acc in (cur_day, week):
                if acc is not None:
                    acc.missing += 1
        else:
            for acc in (cur_day, week):
                if acc is not None:
                    acc.files += 1
            _scan(text, at, matcher, cur_day, week, sha_cache)
            text = None
        sha_cache.clear()
        if progress is not None and (i % PROGRESS_EVERY == 0 or i == total):
            progress(phase="files", done=i, total=total, current=_iso(at),
                     rows_written=written)

    flush_day()
    while wi < len(weeks):
        flush_week(weeks[wi])
        wi += 1
    log.info("gkg %s..%s: %d rows written from %d files", start, end, written, total)
    return written


def _scan(text: str, file_at: datetime, matcher: _Matcher, day, week, sha_cache) -> None:
    for cols in _web_rows(text):
        title = _title(cols[COL_EXTRAS])
        anywhere = None
        if day is not None:
            day.total += 1
            anywhere = matcher.anywhere(cols, title)
            if anywhere:
                tone = _tone(cols[COL_TONE])
                for theme in anywhere:
                    day.volume[theme] += 1
                    if tone is not None:
                        day.tone_sum[theme] += tone
                        day.tone_n[theme] += 1
        if week is not None and title:
            for theme in matcher.in_title(cols, title, anywhere):
                week.add(theme, cols, title, file_at, sha_cache)


# -- CLI ----------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m trend_research.ingest_gkg",
        description="Backfill GDELT theme timelines and titles from raw GKG files.")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--articles-start", type=date.fromisoformat, default=None,
                        help="first day of the weekly titles (default --start)")
    parser.add_argument("--sample-every", type=int, default=4, choices=SAMPLE_EVERY_CHOICES,
                        help="read every Nth 15-minute file from 00:00 UTC (default 4)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    total = backfill_gkg(args.start, args.end, articles_start=args.articles_start,
                         sample_every=args.sample_every)
    print("gkg backfill %s..%s: %d rows written" % (args.start, args.end, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
