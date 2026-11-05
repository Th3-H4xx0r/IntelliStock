"""TR global context (Task 3): GDELT themes, ALFRED macro vintages, event calendar.

Fake HTTP only -- never the network. GDELT is paced at >= 15 s between
requests; a theme-day row becomes available one hour after its UTC day ends;
article titles land in TrendNewsPIT stamped at their ``seendate`` (ruling R1).
ALFRED is read as one keyless snapshot per calendar day, so a value's vintage
is the first day it appears, and ``macro_as_of`` shows what was published
before the decision -- the first release until the revision is out.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from trend_research import calendar_data  # noqa: E402
from trend_research import ingest_gdelt as gd  # noqa: E402
from trend_research import ingest_macro as im  # noqa: E402
from trend_research import pit_store  # noqa: E402

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


# -- GDELT fakes ------------------------------------------------------------------

class FakeGdelt:
    """Answers timelinevolraw / timelinetone / artlist from per-day tables.

    ``volume[day]`` and ``tone[day]`` are daily points; ``articles[day]`` is a
    list of (url, title, seendate-datetime). Records every call and the fake
    clock time it was made at.
    """

    def __init__(self, clock=None, *, volume=None, tone=None, articles=None,
                 failures=None):
        self.clock = clock
        self.volume = volume or {}
        self.tone = tone or {}
        self.articles = articles or {}
        self.failures = list(failures or [])
        self.calls = []

    def _days(self, params):
        lo = datetime.strptime(params["startdatetime"], "%Y%m%d%H%M%S").date()
        hi = datetime.strptime(params["enddatetime"], "%Y%m%d%H%M%S").date()
        d = lo
        while d <= hi:
            yield d
            d += timedelta(days=1)

    def __call__(self, url, *, params, headers, timeout):
        self.calls.append({"url": url, "params": dict(params),
                           "t": self.clock.now if self.clock else None})
        if self.failures:
            return self.failures.pop(0)
        mode = params["mode"]
        if mode == "timelinevolraw":
            data = [{"date": ymd(d) + "T000000Z", "value": self.volume[d],
                     "norm": 100000} for d in self._days(params) if d in self.volume]
            body = {"query_details": {"title": params["query"]},
                    "timeline": [{"series": "Article Count", "data": data}]}
        elif mode == "timelinetone":
            data = [{"date": ymd(d) + "T000000Z", "value": self.tone[d]}
                    for d in self._days(params) if d in self.tone]
            body = {"query_details": {"title": params["query"]},
                    "timeline": [{"series": "Average Tone", "data": data}]}
        elif mode == "artlist":
            arts = []
            for d in self._days(params):
                for url_, title, seen in self.articles.get(d, []):
                    arts.append({"url": url_, "url_mobile": "", "title": title,
                                 "seendate": seen.strftime("%Y%m%dT%H%M%SZ"),
                                 "socialimage": "", "domain": "example.com",
                                 "language": "English",
                                 "sourcecountry": "United States"})
            body = {"articles": arts[:int(params["maxrecords"])]} if arts else {}
        else:
            raise AssertionError(mode)
        return FakeResponse(200, json.dumps(body))


def run_gdelt(store, http, clock, start, end, themes=("inflation",), now=NOW, **kw):
    return gd.backfill_themes(start, end, themes=list(themes), http_get=http,
                              sleep=clock.sleep, clock=clock.clock, now=now,
                              store=store, **kw)


def theme_rows(store):
    return list(store.iter(store.order_by(
        store.between("TrendThemesPIT", "0", "~"), index="id")))


def news_rows(store):
    return list(store.iter(store.order_by(
        store.between("TrendNewsPIT", "0", "~"), index="id")))


D1 = date(2022, 6, 14)
D2 = date(2022, 6, 15)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def by_date(store, lo, hi):
    return {r["date"]: r for r in theme_rows(store) if lo.isoformat() <= r["date"] <= hi.isoformat()}


def calls_of(http, mode):
    return [c for c in http.calls if c["params"]["mode"] == mode]


# Week of Mon 2022-06-20, an NYSE holiday (Juneteenth observed): the decision is
# Tue 09:00 ET; the previous week's was Mon 06-13 09:00 ET, so the titles
# window is (2022-06-13 13:00Z, 2022-06-21 13:00Z] (ruling R12b).
W_END = utc(2022, 6, 21, 13, 0)
W_START = utc(2022, 6, 13, 13, 0)


def test_gdelt_spacing(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={D1: 10, D2: 20}, tone={D1: -1.0, D2: -2.0},
                     articles={D2: [("https://a.example/1", "Fed hikes", utc(2022, 6, 15, 18, 15))]})
    run_gdelt(store, http, clock, D1, D2, themes=("inflation", "central_banks"))
    assert len(calls_of(http, "artlist")) == 2           # one week x 2 themes
    times = [c["t"] for c in http.calls]
    assert len(times) == 6                               # + 2 themes x (vol + tone)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 15.0
    assert all(c["url"] == gd.DOC_URL for c in http.calls)
    assert all(c["params"]["format"] == "json" for c in http.calls)


def test_gdelt_retries_429_with_backoff_and_keeps_spacing(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={D1: 5}, tone={D1: 0.5},
                     failures=[FakeResponse(429, "Please limit requests to one every 5 seconds"),
                               FakeResponse(200, "Please limit requests to one every 5 seconds")])
    run_gdelt(store, http, clock, D1, D1, articles=False)
    assert by_date(store, D1, D1)[D1.isoformat()]["volume"] == 5
    times = [c["t"] for c in http.calls]
    assert min(b - a for a, b in zip(times, times[1:])) >= 15.0
    assert max(clock.sleeps) >= gd.BACKOFF_BASE


def test_gdelt_rejected_query_raises_without_retry(store):
    clock = FakeClock()
    http = FakeGdelt(clock, failures=[FakeResponse(200, "Your search contained a keyword that was too short")])
    with pytest.raises(RuntimeError, match="too short"):
        run_gdelt(store, http, clock, D1, D1, articles=False)
    assert len(http.calls) == 1


def test_theme_row_available_next_day(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={D1: 10, D2: 20}, tone={D1: -1.0, D2: -2.5})
    run_gdelt(store, http, clock, D1, D2, articles=False)
    r = by_date(store, D2, D2)["2022-06-15"]
    assert r["id"] == "2022-06-15|inflation"
    assert r["available_at"] == "2022-06-16T01:00:00Z"
    assert r["volume"] == 20 and r["total_articles"] == 100000
    assert r["tone"] == pytest.approx(-2.5)
    assert r["share"] == pytest.approx(20 / 100000)
    assert r["theme"] == "inflation" and r["date"] == "2022-06-15"

    end_of_day = utc(2022, 6, 16, 1, 0)
    seen = gd.themes_between(D1, D2, end_of_day, store=store)
    assert [x["date"] for x in seen] == ["2022-06-14"]           # strict <
    seen = gd.themes_between(D1, D2, end_of_day + timedelta(seconds=1), store=store)
    assert [x["date"] for x in seen] == ["2022-06-14", "2022-06-15"]


def test_timeline_fetches_the_calendar_year_and_marks_it_done(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={D1: 10}, tone={D1: -1.0})
    run_gdelt(store, http, clock, D1, D2, articles=False)
    vol = calls_of(http, "timelinevolraw")
    assert [(c["params"]["startdatetime"], c["params"]["enddatetime"]) for c in vol] == \
        [("20220101000000", "20221231235959")]
    rows = theme_rows(store)
    assert len(rows) == 365 and rows[-1]["date"] == "2022-12-31"   # final year: through Dec 31
    assert by_date(store, D2, D2)["2022-06-15"]["volume"] == 0      # skipped day: zero
    assert by_date(store, D2, D2)["2022-06-15"]["tone"] is None
    assert pit_store.is_done("gdelt_timeline", "inflation|2022", store=store)


def test_year_in_progress_waits_for_trailing_days_and_is_refetched(store):
    a, b = date(2026, 11, 1), date(2026, 11, 3)
    data = dict(volume={a: 1, b: 2}, tone={a: 0.0, b: 0.0})
    clock = FakeClock()
    run_gdelt(store, FakeGdelt(clock, **data), clock, a, date(2026, 11, 4), articles=False)
    dates = [r["date"] for r in theme_rows(store)]
    assert dates[-1] == "2026-11-03" and "2026-11-04" not in dates  # after the last point
    assert not pit_store.is_done("gdelt_timeline", "inflation|2026", store=store)
    again = FakeGdelt(clock, **data)
    run_gdelt(store, again, clock, a, date(2026, 11, 4), articles=False)
    assert len(calls_of(again, "timelinevolraw")) == 1             # no stall: refetched


def test_empty_timeline_writes_nothing_and_is_not_marked(store):
    clock = FakeClock()
    assert run_gdelt(store, FakeGdelt(clock), clock, D1, D2, articles=False) == 0
    assert theme_rows(store) == []
    assert not pit_store.is_done("gdelt_timeline", "inflation|2022", store=store)


def test_incomplete_day_is_not_written(store):
    clock = FakeClock()
    today = NOW.date()
    yesterday = today - timedelta(days=1)
    http = FakeGdelt(clock, volume={yesterday: 3, today: 4}, tone={yesterday: 0.1, today: 0.2})
    run_gdelt(store, http, clock, yesterday, today, articles=False)
    dates = [r["date"] for r in theme_rows(store)]
    assert yesterday.isoformat() in dates and today.isoformat() not in dates


def test_titles_are_weekly_and_available_at_window_end(store):
    clock = FakeClock()
    url = "https://news.example/fed-hikes-75"
    seen = utc(2022, 6, 15, 18, 15)
    many = [("https://news.example/%d" % i, "title %d" % i, seen) for i in range(100)]
    late = ("https://news.example/late", "after the window", W_END + timedelta(minutes=15))
    http = FakeGdelt(clock, volume={D2: 1}, tone={D2: 0.0},
                     articles={D2: [(url, "  Fed hikes 75bp  ", seen)] + many,
                               date(2022, 6, 21): [late]})
    run_gdelt(store, http, clock, D2, D2)
    art = calls_of(http, "artlist")
    assert len(art) == 1
    p = art[0]["params"]
    assert int(p["maxrecords"]) == gd.ARTICLES_PER_THEME_WEEK == 75
    assert p["startdatetime"] == "20220613130001" and p["enddatetime"] == "20220621130000"
    rows = news_rows(store)
    assert len(rows) == 75
    assert all(r["available_at"] == "2022-06-21T13:00:00Z" for r in rows)
    import hashlib
    sid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    row = next(r for r in rows if r["url"] == url)
    assert row["id"] == "2022-06-21T13:00:00Z|gdelt|%s" % sid
    assert row["source"] == "gdelt" and row["source_id"] == sid
    assert row["published_at"] == "2022-06-15T18:15:00Z"
    assert row["headline"] == "Fed hikes 75bp"
    assert row["domain"] == "example.com" and row["theme"] == "inflation"
    assert row["symbols"] == []
    assert row["window_start"] == "2022-06-13T13:00:00Z"


def test_title_window_is_open_at_start_and_closed_at_end(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={D2: 1}, tone={D2: 0.0},
                     articles={date(2022, 6, 13): [("https://x.example/start", "start", W_START)],
                               date(2022, 6, 21): [("https://x.example/end", "end", W_END),
                                                   ("https://x.example/late", "late",
                                                    W_END + timedelta(minutes=15))]})
    run_gdelt(store, http, clock, D2, D2)
    assert [r["url"] for r in news_rows(store)] == ["https://x.example/end"]


def test_title_windows_tile_a_holiday_week_with_no_gap_or_overlap(store):
    # Mondays 06-13, 06-20 (Juneteenth -> Tue 06-21) and 06-27, 2022.
    clock = FakeClock()
    boundary = W_END                                   # the holiday week's decision
    old_gap = utc(2022, 6, 14, 10, 0)                  # a fixed 7-day window missed this
    after = utc(2022, 6, 22, 10, 0)                    # a fixed 7-day window saw this twice
    arts = {date(2022, 6, 14): [("https://t.example/gap", "gap", old_gap)],
            date(2022, 6, 21): [("https://t.example/edge", "edge", boundary)],
            date(2022, 6, 22): [("https://t.example/after", "after", after)]}
    http = FakeGdelt(clock, volume={D2: 1}, tone={D2: 0.0}, articles=arts)
    run_gdelt(store, http, clock, date(2022, 6, 13), date(2022, 6, 27))
    windows = sorted((c["params"]["startdatetime"], c["params"]["enddatetime"])
                     for c in calls_of(http, "artlist"))
    for (s1, e1), (s2, e2) in zip(windows, windows[1:]):
        prev_end = datetime.strptime(e1, "%Y%m%d%H%M%S")
        next_start = datetime.strptime(s2, "%Y%m%d%H%M%S")
        assert next_start - prev_end == timedelta(seconds=1)        # tiles exactly
    assert ("20220613130001", "20220621130000") in windows
    assert ("20220621130001", "20220627130000") in windows
    rows = news_rows(store)
    by_url = {}
    for r in rows:
        by_url.setdefault(r["url"], []).append(r["available_at"])
    assert by_url == {"https://t.example/gap": ["2022-06-21T13:00:00Z"],
                      "https://t.example/edge": ["2022-06-21T13:00:00Z"],
                      "https://t.example/after": ["2022-06-27T13:00:00Z"]}


def test_week_decision_time_moves_to_first_session_after_a_monday_holiday():
    # Labor Day 2022-09-05: the week's first session is Tuesday.
    assert gd.week_decision_time(date(2022, 9, 5)) == utc(2022, 9, 6, 13, 0)
    assert gd.week_decision_time(date(2022, 6, 20)) == utc(2022, 6, 21, 13, 0)   # Juneteenth
    assert gd.week_decision_time(date(2022, 6, 27)) == utc(2022, 6, 27, 13, 0)   # plain Monday
    assert gd.week_decision_time(date(2022, 12, 5)) == utc(2022, 12, 5, 14, 0)   # EST


def test_articles_without_url_or_seendate_are_skipped(store):
    clock = FakeClock()
    timeline = FakeGdelt(clock, volume={D2: 1}, tone={D2: 0.0})

    def http(url, *, params, headers, timeout):
        if params["mode"] != "artlist":
            return timeline(url, params=params, headers=headers, timeout=timeout)
        body = {"articles": [{"url": "", "title": "x", "seendate": "20220615T120000Z"},
                             {"url": "https://ok.example/1", "title": "x", "seendate": "garbage"},
                             {"url": "https://ok.example/2", "title": "ok",
                              "seendate": "20220615T120000Z", "domain": "ok.example"}]}
        return FakeResponse(200, json.dumps(body))

    run_gdelt(store, http, clock, D2, D2)
    assert [r["url"] for r in news_rows(store)] == ["https://ok.example/2"]


def test_rerun_makes_no_requests(store):
    clock = FakeClock()
    data = dict(volume={D1: 1, D2: 2}, tone={D1: 0.0, D2: 0.0},
                articles={D2: [("https://a.example/2", "t", utc(2022, 6, 15, 18, 15))]})
    first = FakeGdelt(clock, **data)
    assert run_gdelt(store, first, clock, D1, D2) == 365 + 1
    again = FakeGdelt(clock, **data)
    assert run_gdelt(store, again, clock, D1, D2) == 0
    assert again.calls == []


def test_empty_title_week_is_marked_and_not_refetched(store):
    clock = FakeClock()
    data = dict(volume={D2: 1}, tone={D2: 0.0})
    run_gdelt(store, FakeGdelt(clock, **data), clock, D2, D2)
    assert pit_store.is_done("gdelt_titles", "inflation|2022-06-20", store=store)
    again = FakeGdelt(clock, **data)
    run_gdelt(store, again, clock, D2, D2)
    assert calls_of(again, "artlist") == []


def test_articles_start_limits_titles_not_timelines(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={date(2021, 3, 1): 1, D2: 1}, tone={})
    run_gdelt(store, http, clock, date(2021, 3, 1), D2, articles_start=D2)
    assert [c["params"]["startdatetime"][:4] for c in calls_of(http, "timelinevolraw")] == \
        ["2021", "2022"]
    assert [c["params"]["enddatetime"] for c in calls_of(http, "artlist")] == ["20220621130000"]


def test_unknown_theme_and_inverted_range_rejected(store):
    clock = FakeClock()
    with pytest.raises(ValueError):
        run_gdelt(store, FakeGdelt(clock), clock, D1, D2, themes=("no_such_theme",))
    with pytest.raises(ValueError):
        run_gdelt(store, FakeGdelt(clock), clock, D2, D1)
    with pytest.raises(ValueError):
        run_gdelt(store, FakeGdelt(clock), clock, D1, D2, articles_start=D2 + timedelta(days=1))


def test_themes_constant_covers_spec_5_1():
    assert 20 <= len(gd.THEMES) <= 30
    assert set(gd.THEMES) == set(gd.THEME_QUERIES)
    for name in ("ai_semiconductors", "data_centers_power", "war_conflict", "tariffs",
                 "sanctions", "elections", "oil_opec", "inflation", "central_banks",
                 "dollar", "recession", "glp1_obesity", "pandemic", "consumer_spending",
                 "housing", "crypto", "china", "energy_transition", "metals_mining",
                 "defense"):
        assert name in gd.THEME_QUERIES, name
    for name, query in gd.THEME_QUERIES.items():
        assert "|" not in name and query.strip()


# -- ALFRED fakes ------------------------------------------------------------------

class FakeAlfred:
    """Serves alfredgraph.csv the way ALFRED does: one column per requested
    vintage date (at most 12), each holding the values in effect that day.

    ``releases[series][obs_date]`` is a list of (vintage_date, value).
    """

    def __init__(self, releases, clock=None):
        self.releases = releases
        self.clock = clock
        self.calls = []

    def __call__(self, url, *, params, headers, timeout):
        self.calls.append({"url": url, "params": dict(params)})
        ids = params["id"].split(",")
        vintages = [date.fromisoformat(v) for v in params["vintage_date"].split(",")]
        cosd = date.fromisoformat(params["cosd"].split(",")[0])
        ids, vintages = ids[:12], vintages[:12]
        series = ids[0]
        obs_dates = sorted(o for o in self.releases.get(series, {}) if o >= cosd)
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["observation_date"] + ["%s_%s" % (series, ymd(v)) for v in vintages])
        for o in obs_dates:
            cells = []
            for v in vintages:
                vals = [val for vd, val in self.releases[series][o] if vd <= v]
                cells.append(vals[-1] if vals else "")
            if any(cells):
                w.writerow([o.isoformat()] + cells)
        return FakeResponse(200, out.getvalue())


PAYEMS = {
    date(2022, 4, 1): [(date(2022, 5, 6), "151292")],
    date(2022, 5, 1): [(date(2022, 6, 3), "151682"), (date(2022, 7, 8), "151608")],
    date(2022, 6, 1): [(date(2022, 7, 8), "151980")],
}


def run_macro(store, releases, start, end, series=("PAYEMS",), now=NOW):
    clock = FakeClock()
    http = FakeAlfred(releases)
    n = im.backfill_macro(start, end, series=series, http_get=http, sleep=clock.sleep,
                          clock=clock.clock, now=now, store=store)
    return n, http, clock


def et(d: date, hh: int, mm: int) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ZoneInfo("America/New_York"))


def macro_ids(store, series="PAYEMS"):
    return {r["id"]: r for r in store.iter(store.between("TrendMacroPIT", series + "|", series + "}"))}


def test_macro_uses_first_release(store):
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))

    rows = {r["obs_date"]: r for r in im.macro_as_of(
        "PAYEMS", et(date(2022, 6, 20), 9, 30), store=store)}
    assert rows["2022-05-01"]["value"] == 151682          # first release
    assert rows["2022-05-01"]["vintage_date"] == "2022-06-03"
    assert rows["2022-05-01"]["vintage_exact"] is True
    assert "2022-06-01" not in rows                        # not yet released

    at_release = {r["obs_date"]: r for r in im.macro_as_of(
        "PAYEMS", et(date(2022, 7, 8), 8, 30), store=store)}
    assert at_release["2022-05-01"]["value"] == 151682     # strict <: 08:30 ET not yet
    after = im.macro_as_of("PAYEMS", et(date(2022, 7, 8), 8, 31), store=store)
    by_obs = {r["obs_date"]: r for r in after}
    assert by_obs["2022-05-01"]["value"] == 151608          # the revision
    assert by_obs["2022-06-01"]["value"] == 151980
    assert [r["obs_date"] for r in after] == sorted(by_obs)


def test_macro_release_series_available_0830_et(store):
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    row = macro_ids(store)["PAYEMS|2022-05-01|2022-06-03"]
    assert row["available_at"] == "2022-06-03T12:30:00Z"   # 08:30 EDT
    assert row["series"] == "PAYEMS" and row["source"] == "alfred"


def test_macro_daily_market_series_available_1800_et(store):
    dgs10 = {date(2022, 7, 1): [(date(2022, 7, 5), "2.88")],
             date(2022, 12, 1): [(date(2022, 12, 2), "3.53")]}
    run_macro(store, {"DGS10": dgs10}, date(2022, 7, 1), date(2022, 12, 31), series=("DGS10",))
    rows = macro_ids(store, "DGS10")
    assert rows["DGS10|2022-07-01|2022-07-05"]["available_at"] == "2022-07-05T22:00:00Z"  # EDT
    assert rows["DGS10|2022-12-01|2022-12-02"]["available_at"] == "2022-12-02T23:00:00Z"  # EST
    assert im.macro_as_of("DGS10", et(date(2022, 7, 5), 17, 59), store=store) == []
    assert im.macro_as_of("DGS10", et(date(2022, 7, 5), 18, 1), store=store)[0]["value"] == 2.88
    assert "DGS10" in im.DAILY_MARKET_SERIES and "PAYEMS" not in im.DAILY_MARKET_SERIES


def test_macro_values_present_at_first_snapshot_are_marked_inexact(store):
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    rows = macro_ids(store)
    first = im._chunk_start(date(2022, 6, 1)).isoformat()
    seeded = rows["PAYEMS|2022-04-01|%s" % first]
    assert seeded["vintage_exact"] is False
    assert len(rows) == 4      # 04 seeded, 05 first + revision, 06 first -- no daily repeats


def test_macro_fetches_aligned_twelve_day_chunks(store):
    _, http, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    firsts = [date.fromisoformat(c["params"]["vintage_date"].split(",")[0]) for c in http.calls]
    assert firsts[0] == im._chunk_start(date(2022, 6, 1)) <= date(2022, 6, 1)
    assert all(b - a == timedelta(days=12) for a, b in zip(firsts, firsts[1:]))
    assert firsts[-1] <= date(2022, 7, 31) < firsts[-1] + timedelta(days=12)
    assert all(c["params"]["id"] == ",".join(["PAYEMS"] * 12) for c in http.calls)
    assert all(c["url"] == im.ALFRED_URL for c in http.calls)


def test_macro_rerun_makes_no_requests(store):
    n1, _, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    n2, http, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    assert n1 == 4 and n2 == 0
    assert http.calls == []


def test_macro_narrow_then_wide_range_fills_the_earlier_part(store):
    _, narrow, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 7, 1), date(2022, 7, 31))
    _, wide, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 5, 1), date(2022, 7, 31))
    asked = lambda h: {c["params"]["vintage_date"].split(",")[0] for c in h.calls}  # noqa: E731
    assert wide.calls and not (asked(narrow) & asked(wide))      # only the missing chunks
    rows = {r["obs_date"]: r for r in im.macro_as_of(
        "PAYEMS", et(date(2022, 6, 20), 9, 30), store=store)}
    assert rows["2022-05-01"]["value"] == 151682
    assert rows["2022-05-01"]["vintage_date"] == "2022-06-03"
    assert rows["2022-04-01"]["value"] == 151292


def test_macro_extending_forward_seeds_from_the_marked_chunk(store):
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 6, 30))
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    from db.fake import FakeStore
    fresh = FakeStore()
    run_macro(fresh, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    assert set(macro_ids(store)) == set(macro_ids(fresh))         # no spurious rows
    assert all(r["vintage_exact"] for k, r in macro_ids(store).items()
               if r["vintage_date"] > "2022-06-03")


def test_macro_truncated_columns_raise(store):
    def short(url, *, params, headers, timeout):
        return FakeResponse(200, "observation_date,PAYEMS_20220601\n2022-04-01,1\n")

    with pytest.raises(RuntimeError, match="column"):
        im.backfill_macro(date(2022, 6, 1), date(2022, 6, 30), series=("PAYEMS",),
                          http_get=short, sleep=lambda s: None, now=NOW, store=store)


def test_macro_non_csv_response_raises(store):
    def html(url, *, params, headers, timeout):
        return FakeResponse(200, "<html>Series does not exist</html>")

    with pytest.raises(RuntimeError, match="PAYEMS"):
        im.backfill_macro(date(2022, 6, 1), date(2022, 6, 30), series=("PAYEMS",),
                          http_get=html, sleep=lambda s: None, now=NOW, store=store)


def test_macro_does_not_snapshot_future_days_and_refetches_that_chunk(store):
    now = datetime(2022, 6, 10, 12, 0, tzinfo=timezone.utc)
    _, http, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31),
                           now=now)
    requested = [v for c in http.calls for v in c["params"]["vintage_date"].split(",")]
    assert max(requested) == "2022-06-10"
    _, later, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    assert later.calls[0]["params"]["vintage_date"].split(",")[0] == \
        im._chunk_start(date(2022, 6, 10)).isoformat()


def test_macro_series_default_covers_required():
    for s in ("CPIAUCSL", "UNRATE", "PAYEMS", "FEDFUNDS", "DGS10", "DGS2", "T10Y2Y",
              "BAMLH0A0HYM2", "DCOILWTICO", "DTWEXBGS"):
        assert s in im.MACRO_SERIES


# -- calendar ------------------------------------------------------------------------

def test_calendar_contains_known_fomc():
    events = calendar_data.event_calendar(date(2022, 3, 1), date(2022, 3, 31))
    fomc = [e for e in events if e["kind"] == "fomc"]
    assert [e["date"] for e in fomc] == ["2022-03-16"]
    assert fomc[0]["available_at"] == "2022-02-14T00:00:00Z"   # 30 days before
    kinds = {(e["date"], e["kind"]) for e in events}
    assert ("2022-03-10", "cpi") in kinds and ("2022-03-04", "jobs") in kinds


def test_calendar_covers_2020_to_2026_and_is_sorted():
    events = calendar_data.event_calendar(date(2020, 1, 1), date(2026, 12, 31))
    by_kind = {}
    for e in events:
        by_kind.setdefault(e["kind"], []).append(e)
        datetime.fromisoformat(e["available_at"].replace("Z", "+00:00"))
    assert len(by_kind["fomc"]) == 8 + 8 * 6               # 2020: 7 held + 03-18 cancelled
    assert [e["date"] for e in by_kind["fomc"] if e.get("cancelled")] == ["2020-03-18"]
    assert len(by_kind["cpi"]) >= 80 and len(by_kind["jobs"]) >= 80
    assert [e["date"] for e in events] == sorted(e["date"] for e in events)


def test_calendar_bounds_are_inclusive_and_rescheduled_dates_announced_late():
    assert [e["date"] for e in calendar_data.event_calendar(
        date(2022, 3, 16), date(2022, 3, 16))] == ["2022-03-16"]
    moved = [e for e in calendar_data.event_calendar(date(2025, 11, 20), date(2025, 11, 20))
             if e["kind"] == "jobs"]
    assert moved and moved[0]["available_at"] > "2025-10-21T00:00:00Z"
    with pytest.raises(ValueError):
        calendar_data.event_calendar(date(2022, 3, 2), date(2022, 3, 1))


def test_calendar_shows_a_cancellation_only_once_it_was_public():
    def fomc_on(decision_time):
        return [e for e in calendar_data.event_calendar(date(2020, 3, 1), date(2020, 3, 31),
                                                        decision_time)
                if e["kind"] == "fomc"]

    assert fomc_on(utc(2020, 2, 16)) == []                       # not yet on the calendar
    before = fomc_on(utc(2020, 3, 9, 13, 30))
    assert [e["date"] for e in before] == ["2020-03-18"]
    assert "cancelled" not in before[0] and "cancelled_available_at" not in before[0]
    at = fomc_on(utc(2020, 3, 15, 21, 0))                        # strict <
    assert "cancelled" not in at[0]
    after = fomc_on(utc(2020, 3, 16, 13, 30))
    assert after[0]["cancelled"] is True
    assert after[0]["cancelled_available_at"] == "2020-03-15T21:00:00Z"

    oct25 = calendar_data.event_calendar(date(2025, 10, 1), date(2025, 10, 31),
                                         utc(2025, 10, 20, 13, 30))
    cpi = {e["date"]: e for e in oct25 if e["kind"] == "cpi"}
    assert cpi["2025-10-15"]["cancelled"] is True                # original date, gone
    assert "2025-10-24" not in cpi                               # new date: known 1 day ahead
    jobs = {e["date"]: e for e in oct25 if e["kind"] == "jobs"}
    assert jobs["2025-10-03"]["cancelled"] is True and jobs["2025-10-03"]["ref_month"] == "2025-09"
