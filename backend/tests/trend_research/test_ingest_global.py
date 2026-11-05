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


def test_gdelt_spacing(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={D1: 10, D2: 20}, tone={D1: -1.0, D2: -2.0},
                     articles={D2: [("https://a.example/1", "Fed hikes",
                                     datetime(2022, 6, 15, 18, 15, tzinfo=timezone.utc))]})
    run_gdelt(store, http, clock, D1, D2, themes=("inflation", "central_banks"))
    times = [c["t"] for c in http.calls]
    assert len(times) >= 6                     # 2 themes x (vol + tone) + artlists
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps and min(gaps) >= 15.0
    assert all(c["url"] == gd.DOC_URL for c in http.calls)
    assert all(c["params"]["format"] == "json" for c in http.calls)


def test_gdelt_retries_429_with_backoff_and_keeps_spacing(store):
    clock = FakeClock()
    http = FakeGdelt(clock, volume={D1: 5}, tone={D1: 0.5},
                     failures=[FakeResponse(429, "Please limit requests to one every 5 seconds"),
                               FakeResponse(200, "Please limit requests to one every 5 seconds")])
    run_gdelt(store, http, clock, D1, D1, articles=False)
    assert len(theme_rows(store)) == 1
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
    rows = theme_rows(store)
    assert [r["id"] for r in rows] == ["2022-06-14|inflation", "2022-06-15|inflation"]
    r = rows[1]
    assert r["available_at"] == "2022-06-16T01:00:00Z"
    assert r["volume"] == 20 and r["total_articles"] == 100000
    assert r["tone"] == pytest.approx(-2.5)
    assert r["share"] == pytest.approx(20 / 100000)
    assert r["theme"] == "inflation" and r["date"] == "2022-06-15"

    end_of_day = datetime(2022, 6, 16, 1, 0, tzinfo=timezone.utc)
    seen = gd.themes_between(D1, D2, end_of_day, store=store)
    assert [x["date"] for x in seen] == ["2022-06-14"]           # strict <
    seen = gd.themes_between(D1, D2, end_of_day + timedelta(seconds=1), store=store)
    assert [x["date"] for x in seen] == ["2022-06-14", "2022-06-15"]
    assert gd.themes_between(D2, D2, end_of_day + timedelta(days=9),
                             store=store)[0]["date"] == "2022-06-15"


def test_gap_in_timeline_is_zero_volume_but_trailing_days_wait(store):
    clock = FakeClock()
    D0, D3 = D1 - timedelta(days=1), D2 + timedelta(days=1)
    http = FakeGdelt(clock, volume={D0: 7, D2: 10}, tone={D0: 1.0, D2: -1.0})
    run_gdelt(store, http, clock, D0, D3, articles=False)
    rows = {r["date"]: r for r in theme_rows(store)}
    assert rows["2022-06-14"]["volume"] == 0           # gap: zero
    assert rows["2022-06-14"]["tone"] is None
    assert "2022-06-16" not in rows                    # after the last point: not yet


def test_empty_timeline_writes_nothing(store):
    clock = FakeClock()
    http = FakeGdelt(clock)
    assert run_gdelt(store, http, clock, D1, D2, articles=False) == 0
    assert theme_rows(store) == []


def test_incomplete_day_is_not_written(store):
    clock = FakeClock()
    today = NOW.date()
    yesterday = today - timedelta(days=1)
    http = FakeGdelt(clock, volume={yesterday: 3, today: 4}, tone={yesterday: 0.1, today: 0.2},
                     articles={today: [("https://a.example/t", "today",
                                        NOW - timedelta(hours=1))]})
    run_gdelt(store, http, clock, yesterday, today)
    assert [r["date"] for r in theme_rows(store)] == [yesterday.isoformat()]
    assert all(c["params"]["startdatetime"][:8] != ymd(today)
               for c in http.calls if c["params"]["mode"] == "artlist")


def test_articles_go_to_news_table_per_ruling_r1(store):
    clock = FakeClock()
    seen = datetime(2022, 6, 15, 18, 15, tzinfo=timezone.utc)
    url = "https://news.example/fed-hikes-75"
    many = [("https://news.example/%d" % i, "title %d" % i, seen) for i in range(40)]
    http = FakeGdelt(clock, volume={D2: 1}, tone={D2: 0.0},
                     articles={D2: [(url, "  Fed hikes 75bp  ", seen)] + many})
    run_gdelt(store, http, clock, D2, D2)
    art_calls = [c for c in http.calls if c["params"]["mode"] == "artlist"]
    assert len(art_calls) == 1
    assert int(art_calls[0]["params"]["maxrecords"]) == gd.ARTICLES_PER_THEME_DAY == 25
    rows = news_rows(store)
    assert len(rows) == 25
    import hashlib
    sid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    row = next(r for r in rows if r["url"] == url)
    assert row["id"] == "2022-06-15T18:15:00Z|gdelt|%s" % sid
    assert row["source"] == "gdelt" and row["source_id"] == sid
    assert row["available_at"] == "2022-06-15T18:15:00Z"
    assert row["headline"] == "Fed hikes 75bp"
    assert row["domain"] == "example.com" and row["theme"] == "inflation"
    assert row["symbols"] == []


def test_articles_without_url_or_seendate_are_skipped(store):
    clock = FakeClock()

    def http(url, *, params, headers, timeout):
        if params["mode"] != "artlist":
            return FakeGdelt(clock, volume={D2: 1}, tone={D2: 0.0})(
                url, params=params, headers=headers, timeout=timeout)
        body = {"articles": [{"url": "", "title": "x", "seendate": "20220615T120000Z"},
                             {"url": "https://ok.example/1", "title": "x", "seendate": "garbage"},
                             {"url": "https://ok.example/2", "title": "ok",
                              "seendate": "20220615T120000Z", "domain": "ok.example"}]}
        return FakeResponse(200, json.dumps(body))

    run_gdelt(store, http, clock, D2, D2)
    assert [r["url"] for r in news_rows(store)] == ["https://ok.example/2"]


def test_rerun_skips_completed_chunks_and_article_days(store):
    seen = datetime(2022, 6, 15, 18, 15, tzinfo=timezone.utc)
    clock = FakeClock()
    data = dict(volume={D1: 1, D2: 2}, tone={D1: 0.0, D2: 0.0},
                articles={D1: [("https://a.example/1", "t", seen - timedelta(days=1))],
                          D2: [("https://a.example/2", "t", seen)]})
    first = FakeGdelt(clock, **data)
    assert run_gdelt(store, first, clock, D1, D2) == 4      # 2 theme rows + 2 articles
    again = FakeGdelt(clock, **data)
    assert run_gdelt(store, again, clock, D1, D2) == 0
    assert again.calls == []


def test_unknown_theme_and_inverted_range_rejected(store):
    clock = FakeClock()
    with pytest.raises(ValueError):
        run_gdelt(store, FakeGdelt(clock), clock, D1, D2, themes=("no_such_theme",))
    with pytest.raises(ValueError):
        run_gdelt(store, FakeGdelt(clock), clock, D2, D1)


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


def test_macro_uses_first_release(store):
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))

    rows ={r["obs_date"]: r for r in im.macro_as_of(
        "PAYEMS", et(date(2022, 6, 20), 9, 30), store=store)}
    assert rows["2022-05-01"]["value"] == 151682          # first release
    assert rows["2022-05-01"]["vintage_date"] == "2022-06-03"
    assert rows["2022-05-01"]["vintage_exact"] is True
    assert "2022-06-01" not in rows                        # not yet released

    at_release = {r["obs_date"]: r for r in im.macro_as_of(
        "PAYEMS", et(date(2022, 7, 8), 8, 30), store=store)}
    assert at_release["2022-05-01"]["value"] == 151682     # strict <: 08:30 ET not yet
    after = {r["obs_date"]: r for r in im.macro_as_of(
        "PAYEMS", et(date(2022, 7, 8), 8, 31), store=store)}
    assert after["2022-05-01"]["value"] == 151608          # the revision
    assert after["2022-06-01"]["value"] == 151980
    assert [r["obs_date"] for r in im.macro_as_of(
        "PAYEMS", et(date(2022, 7, 8), 8, 31), store=store)] == sorted(after)


def test_macro_available_at_is_0830_et_in_utc(store):
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    rows = list(store.iter(store.between("TrendMacroPIT", "PAYEMS|", "PAYEMS}")))
    row = next(r for r in rows if r["id"] == "PAYEMS|2022-05-01|2022-06-03")
    assert row["available_at"] == "2022-06-03T12:30:00Z"   # EDT
    assert row["series"] == "PAYEMS" and row["source"] == "alfred"


def test_macro_values_present_at_start_are_marked_inexact(store):
    run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    rows = {r["id"]: r for r in store.iter(store.between("TrendMacroPIT", "PAYEMS|", "PAYEMS}"))}
    seeded = rows["PAYEMS|2022-04-01|2022-06-01"]
    assert seeded["vintage_exact"] is False
    assert len(rows) == 4      # 04 seeded, 05 first + revision, 06 first -- no daily repeats


def test_macro_batches_twelve_vintages_per_call(store):
    _, http, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    assert len(http.calls) == 6                             # 61 days / 12
    first = http.calls[0]["params"]
    assert first["id"] == ",".join(["PAYEMS"] * 12)
    assert first["vintage_date"].split(",")[0] == "2022-06-01"
    assert all(c["url"] == im.ALFRED_URL for c in http.calls)


def test_macro_rerun_is_idempotent_and_resumes(store):
    n1, _, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    n2, http, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31))
    assert n1 == 4 and n2 == 0
    # resumes at the newest stored vintage (2022-07-08), not at the start
    assert http.calls[0]["params"]["vintage_date"].split(",")[0] == "2022-07-08"


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


def test_macro_does_not_snapshot_future_days(store):
    now = datetime(2022, 6, 10, 12, 0, tzinfo=timezone.utc)
    _, http, _ = run_macro(store, {"PAYEMS": PAYEMS}, date(2022, 6, 1), date(2022, 7, 31),
                           now=now)
    requested = [v for c in http.calls for v in c["params"]["vintage_date"].split(",")]
    assert max(requested) == "2022-06-10"


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
        by_kind.setdefault(e["kind"], []).append(e["date"])
        datetime.fromisoformat(e["available_at"].replace("Z", "+00:00"))
    assert len(by_kind["fomc"]) == 7 + 8 * 6               # 2020 had 7 scheduled
    assert len(by_kind["cpi"]) >= 80 and len(by_kind["jobs"]) >= 80
    assert [e["date"] for e in events] == sorted(e["date"] for e in events)


def test_calendar_bounds_are_inclusive_and_rescheduled_dates_announced_late():
    assert [e["date"] for e in calendar_data.event_calendar(
        date(2022, 3, 16), date(2022, 3, 16))] == ["2022-03-16"]
    shutdown = [e for e in calendar_data.event_calendar(date(2025, 11, 20), date(2025, 11, 20))
                if e["kind"] == "jobs"]
    assert shutdown and shutdown[0]["available_at"] > "2025-10-21T00:00:00Z"
    with pytest.raises(ValueError):
        calendar_data.event_calendar(date(2022, 3, 2), date(2022, 3, 1))
