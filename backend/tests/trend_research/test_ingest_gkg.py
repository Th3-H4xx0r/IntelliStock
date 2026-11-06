"""GDELT themes from the raw GKG 2.1 files (ruling R35).

No network: every GKG file is a zip built in memory from real-shaped
27-column rows and served by a fake ``http_get`` keyed by file stamp.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from trend_research import ingest_gdelt as gd  # noqa: E402
from trend_research import ingest_gkg as gkg  # noqa: E402
from trend_research import jobs, pit_store  # noqa: E402

DAY = date(2025, 6, 4)                       # a Wednesday
MONDAY = date(2025, 6, 9)                    # title window (06-02 13:00Z, 06-09 13:00Z]
W_START = datetime(2025, 6, 2, 13, tzinfo=timezone.utc)
W_END = datetime(2025, 6, 9, 13, tzinfo=timezone.utc)
LATER = datetime(2025, 6, 20, tzinfo=timezone.utc)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def stamp(at: datetime) -> str:
    return at.strftime("%Y%m%d%H%M%S")


def row(at, url, *, title="A plain headline", source="example.com", themes="",
        locations="", persons="", orgs="", tone="1.5,2.0,0.5,2.5,10.0,0.0,300",
        collection="1"):
    cols = [""] * 27
    cols[0] = "%s-%s" % (stamp(at), hashlib.md5(url.encode()).hexdigest()[:6])
    cols[1] = stamp(at)
    cols[2] = collection
    cols[3] = source
    cols[4] = url
    cols[7] = themes
    cols[9] = locations
    cols[11] = persons
    cols[13] = orgs
    cols[15] = tone
    cols[26] = ("<PAGE_LINKS>x</PAGE_LINKS><PAGE_TITLE>%s</PAGE_TITLE>" % title
                if title is not None else "")
    return "\t".join(cols)


def gkg_zip(at, lines) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("%s.gkg.csv" % stamp(at), "".join(l + "\n" for l in lines))
    return buf.getvalue()


class Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeGkg:
    """Serves ``files[stamp] = [row lines]`` (others: an empty file);
    ``plan[stamp] = [status | Exception | "corrupt", ...]`` per attempt."""

    def __init__(self, files=None, *, missing=(), plan=None):
        self.files = {stamp(k) if isinstance(k, datetime) else k: v
                      for k, v in (files or {}).items()}
        self.missing = {stamp(m) for m in missing}
        self.plan = {stamp(k): list(v) for k, v in (plan or {}).items()}
        self.urls = []

    def __call__(self, url, *, timeout):
        assert timeout == gkg.REQUEST_TIMEOUT
        self.urls.append(url)
        name = url.rsplit("/", 1)[1].split(".")[0]
        assert url == gkg.GKG_URL % name
        steps = self.plan.get(name)
        if steps:
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            if step == "corrupt":
                return Resp(200, b"PK\x03\x04 not a zip")
            if step != 200:
                return Resp(step)
        if name in self.missing:
            return Resp(404)
        at = datetime.strptime(name, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return Resp(200, gkg_zip(at, self.files.get(name, [])))

    def stamps(self):
        return [u.rsplit("/", 1)[1].split(".")[0] for u in self.urls]


@pytest.fixture
def st():
    return FakeStore()


def run(st, http, start, end, *, now=LATER, sleeps=None, **kw):
    sleeps = [] if sleeps is None else sleeps
    return gkg.backfill_gkg(start, end, http_get=http, sleep=sleeps.append, now=now,
                            store=st, **kw)


def theme_rows(st):
    return {r["id"]: r for r in st.iter(st.between("TrendThemesPIT", "", "￿"))}


def news_rows(st):
    return sorted(st.iter(st.between("TrendNewsPIT", "", "￿")), key=lambda r: r["id"])


# Day-only runs: the day is available, its title week has not closed.
DAY_NOW = utc(2025, 6, 5, 2)


# -- matching -----------------------------------------------------------------------

def _cols(line):
    return line.split("\t")


def test_theme_token_matches_exact_v1themes_tokens():
    m = gkg._Matcher(["inflation", "trade_dispute"])
    at = utc(2025, 6, 4)
    hit = _cols(row(at, "u1", themes="TAX_X;ECON_INFLATION;"))
    offset = _cols(row(at, "u2", themes="ECON_INFLATION,123;ECON_TRADE_DISPUTE"))
    near = _cols(row(at, "u3", themes="ECON_INFLATION_RATE;XECON_INFLATION"))
    assert m.anywhere(hit, "x") == ["inflation"]
    assert sorted(m.anywhere(offset, "x")) == ["inflation", "trade_dispute"]
    assert m.anywhere(near, "x") == []
    # titles need a non-empty title and the token
    assert m.in_title(hit, "Prices climb") == ["inflation"]
    assert m.in_title(hit, "") == []


def test_keyword_matches_title_orgs_persons_or_location_names_but_titles_need_the_title():
    m = gkg._Matcher(["china", "central_banks", "crypto"])
    at = utc(2025, 6, 4)
    by_loc = _cols(row(at, "u1", locations="1#United States#US#US#1#2#US;"
                                           "4#Beijing, Beijing, China#CH#CH22#3#4#-1"))
    by_org = _cols(row(at, "u2", orgs="federal reserve;world bank"))
    by_person = _cols(row(at, "u3", persons="xi jinping;bitcoin satoshi"))
    by_title = _cols(row(at, "u4"))
    assert m.anywhere(by_loc, "Market wrap") == ["china"]
    assert m.anywhere(by_org, "Market wrap") == ["central_banks"]
    assert m.anywhere(by_person, "Market wrap") == ["crypto"]
    assert m.anywhere(by_title, "The FEDERAL RESERVE holds") == ["central_banks"]
    # the location country code "CH" / "US" fields are not names
    assert m.anywhere(_cols(row(at, "u5", locations="1#Somewhere#CH#CH#1#2#CH")), "x") == []
    # word boundaries: no substring hits
    assert m.anywhere(by_title, "Cryptography and Chinaware") == []
    # titles: only a keyword hit in the title itself counts
    assert m.in_title(by_org, "Market wrap") == []
    assert m.in_title(by_org, "Market wrap", m.anywhere(by_org, "Market wrap")) == []
    assert m.in_title(by_title, "Crypto slides as China data disappoints") == ["china", "crypto"]
    t = "Crypto slides"
    assert m.in_title(by_title, t, m.anywhere(by_title, t)) == ["crypto"]


def test_title_is_unescaped_and_whitespace_collapsed():
    assert gkg._title("<PAGE_TITLE>Fed &amp; ECB\t hold   rates</PAGE_TITLE><X>") == \
        "Fed & ECB hold rates"
    assert gkg._title("<PAGE_LINKS>x</PAGE_LINKS>") == ""


# -- timelines ----------------------------------------------------------------------

def _two_file_day():
    f1, f2 = utc(2025, 6, 4, 0), utc(2025, 6, 4, 2)
    return {
        f1: [row(f1, "https://a.com/1", themes="ECON_INFLATION;", tone="2.0,1,1"),
             row(f1, "https://b.com/2", title="New tariffs announced", tone="-4.0,1,1"),
             row(f1, "https://c.com/3", title="Weather today")],
        f2: [row(f2, "https://d.com/4", themes="ECON_INFLATION", tone="4.0,1,1"),
             row(f2, "https://e.com/5", themes="ECON_INFLATION", collection="2"),
             "\t".join(["short", "row"])],
    }


def test_timeline_rows_for_a_day_with_two_sampled_files(st):
    http = FakeGkg(_two_file_day())
    written = run(st, http, DAY, DAY, now=DAY_NOW, sample_every=8)
    assert written == len(gd.THEMES)
    assert len(http.urls) == 12 and http.stamps()[0] == "20250604000000"
    assert http.stamps()[1] == "20250604020000"
    rows = theme_rows(st)
    assert len(rows) == len(gd.THEMES)
    inf = rows["2025-06-04|inflation"]
    assert inf == {
        "id": "2025-06-04|inflation", "date": "2025-06-04", "theme": "inflation",
        "query": gd.THEME_QUERIES["inflation"], "source": "gdelt", "volume": 2,
        "total_articles": 4, "share": 0.5, "tone": 3.0,
        "available_at": "2025-06-05T01:00:00Z", "via": "gkg", "sample_every": 8,
        "files": 12, "files_missing": 0}
    tar = rows["2025-06-04|tariffs"]
    assert (tar["volume"], tar["tone"], tar["share"]) == (1, -4.0, 0.25)
    quiet = rows["2025-06-04|crypto"]
    assert (quiet["volume"], quiet["tone"], quiet["share"]) == (0, None, 0.0)
    # same keys as the DOC-API path
    doc_keys = set(gd._theme_rows("crypto", [DAY], [(utc(2025, 6, 4), {"value": 1})], [],
                                  final=True)[0])
    assert doc_keys <= set(inf)
    marker = st.get("TrendIngestProgress", "gdelt_gkg_day|2025-06-04")
    assert marker["meta"]["files"] == 12 and marker["meta"]["total_articles"] == 4


def test_a_day_is_not_written_before_it_is_available(st):
    early = gd._theme_available_at(DAY) - timedelta(minutes=1)
    http = FakeGkg(_two_file_day())
    assert run(st, http, DAY, DAY, now=early, sample_every=8) == 0
    assert http.urls == [] and theme_rows(st) == {}
    assert run(st, http, DAY, DAY, now=gd._theme_available_at(DAY), sample_every=8) > 0


def test_a_day_with_over_10pct_missing_files_raises_and_writes_nothing(st):
    missing = [utc(2025, 6, 4, 4), utc(2025, 6, 4, 10)]          # 2 of 12 = 16.7%
    with pytest.raises(RuntimeError, match="2025-06-04"):
        run(st, FakeGkg(_two_file_day(), missing=missing), DAY, DAY, now=DAY_NOW,
            sample_every=8)
    assert theme_rows(st) == {}
    assert not pit_store.is_done("gdelt_gkg_day", "2025-06-04", store=st)
    # one missing of 12 (8.3%) is tolerated and recorded
    run(st, FakeGkg(_two_file_day(), missing=missing[:1]), DAY, DAY, now=DAY_NOW,
        sample_every=8)
    r = theme_rows(st)["2025-06-04|inflation"]
    assert (r["files"], r["files_missing"]) == (11, 1)


def test_404_is_missing_and_5xx_or_network_errors_are_retried(st):
    f = utc(2025, 6, 4, 6)
    sleeps = []
    http = FakeGkg(_two_file_day(), missing=[utc(2025, 6, 4, 8)],
                   plan={f: [500, OSError("reset"), 503]})
    run(st, http, DAY, DAY, now=DAY_NOW, sample_every=8, sleeps=sleeps)
    assert sleeps == [5, 10, 20]
    assert http.stamps().count("20250604060000") == 4
    assert http.stamps().count("20250604080000") == 1           # 404: not retried
    r = theme_rows(st)["2025-06-04|inflation"]
    assert (r["files"], r["files_missing"]) == (11, 1)


def test_persistent_5xx_gives_up_and_other_statuses_raise(st):
    sleeps = []
    with pytest.raises(RuntimeError, match="gave up"):
        run(st, FakeGkg(plan={utc(2025, 6, 4): [500] * 5}), DAY, DAY, now=DAY_NOW,
            sample_every=8, sleeps=sleeps)
    assert sleeps == [5, 10, 20, 40]
    with pytest.raises(RuntimeError, match="HTTP 403"):
        run(st, FakeGkg(plan={utc(2025, 6, 4): [403]}), DAY, DAY, now=DAY_NOW,
            sample_every=8)
    assert theme_rows(st) == {}


def test_a_corrupt_zip_is_retried_then_counted_missing(st):
    sleeps = []
    http = FakeGkg(_two_file_day(), plan={utc(2025, 6, 4, 0): ["corrupt"]})
    run(st, http, DAY, DAY, now=DAY_NOW, sample_every=8, sleeps=sleeps)
    assert sleeps == [5]
    assert theme_rows(st)["2025-06-04|inflation"]["volume"] == 2


def test_sample_every_picks_stamps_from_midnight_and_rejects_other_values(st):
    http = FakeGkg()
    run(st, http, DAY, DAY, now=DAY_NOW, sample_every=4)
    assert len(http.urls) == 24
    assert http.stamps()[:3] == ["20250604000000", "20250604010000", "20250604020000"]
    for bad in (3, 0, True, 16):
        with pytest.raises(ValueError, match="sample_every"):
            run(st, FakeGkg(), DAY, DAY, now=DAY_NOW, sample_every=bad)


# -- titles -------------------------------------------------------------------------

TITLE_START, TITLE_END = date(2025, 6, 3), date(2025, 6, 8)   # one window: week of MONDAY


def test_title_window_excludes_its_start_and_includes_its_end(st):
    inside = W_START + timedelta(hours=1)
    files = {
        W_START: [row(W_START, "https://a.com/start", title="Tariffs at the start")],
        inside: [row(inside, "https://a.com/in", title="Tariffs inside")],
        W_END: [row(W_END, "https://a.com/end", title="Tariffs at the end")],
        W_END + timedelta(hours=1): [row(W_END + timedelta(hours=1), "https://a.com/after",
                                         title="Tariffs after")],
    }
    http = FakeGkg(files)
    run(st, http, TITLE_START, TITLE_END, sample_every=4)
    got = sorted(r["url"] for r in news_rows(st) if r["theme"] == "tariffs")
    assert got == ["https://a.com/end", "https://a.com/in"]
    assert stamp(W_START) not in http.stamps()                  # not this window's file
    assert stamp(W_END) in http.stamps()                        # after `end`: fetched for the window
    assert stamp(W_END + timedelta(hours=1)) not in http.stamps()


def test_title_rows_keep_the_normalize_article_contract(st):
    at = W_START + timedelta(hours=5)
    url = "https://news.example.com/tariffs"
    http = FakeGkg({at: [row(at, url, source="news.example.com",
                             title="Tariffs &amp; trade: what next")]})
    run(st, http, TITLE_START, TITLE_END, sample_every=4)
    (r,) = [x for x in news_rows(st) if x["theme"] == "tariffs"]
    doc = gd.normalize_article({"url": url, "seendate": stamp(at), "title": "t"}, "tariffs",
                               W_START, W_END)
    assert set(doc) <= set(r)
    sid = hashlib.sha256(url.encode()).hexdigest()[:16]
    assert r["id"] == "2025-06-09T13:00:00Z|gdelt|%s" % sid
    assert r["source_id"] == sid and r["source"] == "gdelt" and r["via"] == "gkg"
    assert r["available_at"] == r["window_end"] == "2025-06-09T13:00:00Z"
    assert r["window_start"] == "2025-06-02T13:00:00Z"
    assert r["published_at"] == "2025-06-02T18:00:00Z"
    assert r["headline"] == "Tariffs & trade: what next"
    assert (r["domain"], r["language"], r["sourcecountry"], r["symbols"]) == \
        ("news.example.com", "English", "", [])
    assert r["prominence"] == 1


def test_titles_rank_by_distinct_sources_and_dedupe_by_normalized_headline(st):
    t1, t2 = W_START + timedelta(hours=2), W_START + timedelta(hours=30)
    files = {
        t1: [row(t1, "https://a.com/rise", source="a.com", title="Tariffs rise again"),
             row(t1, "https://a.com/fall1", source="a.com", title="Tariffs fall"),
             row(t1, "https://x.com/steady", source="x.com", title="Tariffs steady")],
        t2: [row(t2, "https://b.com/rise", source="b.com", title="TARIFFS rise again!"),
             row(t2, "https://c.com/rise", source="c.com", title="Tariffs, rise again"),
             row(t2, "https://a.com/fall2", source="a.com", title="Tariffs fall"),
             row(t2, "https://y.com/steady", source="y.com", title="Tariffs steady"),
             # only an organization mention: counts for the timeline, not a title
             row(t2, "https://z.com/org", source="z.com", title="Markets mixed",
                 orgs="tariffs commission")],
    }
    run(st, FakeGkg(files), TITLE_START, TITLE_END, sample_every=4)
    rows = [r for r in news_rows(st) if r["theme"] == "tariffs"]
    ranked = sorted(rows, key=lambda r: -r["prominence"])
    assert [(r["url"], r["prominence"]) for r in ranked] == [
        ("https://a.com/rise", 3), ("https://x.com/steady", 2), ("https://a.com/fall1", 1)]
    meta = st.get("TrendIngestProgress", "gdelt_titles|tariffs|%s" % MONDAY)["meta"]
    assert meta["kept"] == 3 and meta["candidates"] == 7
    assert theme_rows(st)["%s|tariffs" % (t2.date())]["volume"] == 5


def test_titles_are_capped_by_rank_and_every_theme_gets_a_marker(st):
    at, late = W_START + timedelta(hours=3), W_START + timedelta(hours=40)
    urls = ["https://s%d.com/x" % i for i in range(80)]
    lines = [row(at, u, source="s%d.com" % i, title="Tariffs story number %d" % i)
             for i, u in enumerate(urls)]
    # the latest headline, carried by two sources, outranks every single-source one
    late_lines = [row(late, "https://p%d.com/late" % i, source="p%d.com" % i,
                      title="Tariffs late but prominent") for i in range(2)]
    run(st, FakeGkg({at: lines, late: late_lines}), TITLE_START, TITLE_END, sample_every=4)
    tariffs = [r for r in news_rows(st) if r["theme"] == "tariffs"]
    assert len(tariffs) == gd.ARTICLES_PER_THEME_WEEK == 75
    kept = {r["url"] for r in tariffs}
    assert "https://p0.com/late" in kept or "https://p1.com/late" in kept
    # the 74 single-source survivors are the lowest url hashes (all at the same DATE)
    by_hash = sorted(urls, key=lambda u: hashlib.sha256(u.encode()).hexdigest())
    assert kept - {"https://p0.com/late", "https://p1.com/late"} == set(by_hash[:74])
    for theme in gd.THEMES:
        marker = st.get("TrendIngestProgress", "gdelt_titles|%s|%s" % (theme, MONDAY))
        assert marker is not None, theme
        assert set(marker["meta"]) == {"via", "files", "files_missing", "candidates",
                                       "kept", "window_end"}
        assert marker["meta"]["window_end"] == "2025-06-09T13:00:00Z"
        assert marker["meta"]["files"] == 168 and marker["meta"]["files_missing"] == 0
        assert marker["meta"]["kept"] == (75 if theme == "tariffs" else 0)
    meta = st.get("TrendIngestProgress", "gdelt_titles|tariffs|%s" % MONDAY)["meta"]
    assert meta["candidates"] == 82


def test_a_title_window_with_over_10pct_missing_raises_without_markers(st):
    gap = [utc(2025, 6, 5) + timedelta(hours=h) for h in range(0, 48, 2)]   # 24 of 168
    with pytest.raises(RuntimeError, match="title week %s" % MONDAY):
        run(st, FakeGkg(missing=gap), date(2025, 6, 3), date(2025, 6, 3), sample_every=4)
    assert pit_store.done_keys("gdelt_titles", store=st) == set()
    assert pit_store.is_done("gdelt_gkg_day", "2025-06-03", store=st)    # flushed before


def test_a_second_run_downloads_nothing_for_marked_days_and_weeks(st):
    at = W_START + timedelta(hours=3)
    files = {at: [row(at, "https://a.com/1", title="Tariffs story")]}
    first = run(st, FakeGkg(files), TITLE_START, TITLE_END, sample_every=4)
    assert first == 6 * len(gd.THEMES) + 1
    again = FakeGkg(files)
    assert run(st, again, TITLE_START, TITLE_END, sample_every=4) == 0
    assert again.urls == []


def test_a_day_marked_for_fewer_themes_is_aggregated_again(st):
    run(st, FakeGkg(_two_file_day()), DAY, DAY, now=DAY_NOW, sample_every=8,
        themes=["inflation"])
    assert list(theme_rows(st)) == ["2025-06-04|inflation"]
    http = FakeGkg(_two_file_day())
    run(st, http, DAY, DAY, now=DAY_NOW, sample_every=8)
    assert len(http.urls) == 12 and len(theme_rows(st)) == len(gd.THEMES)
    again = FakeGkg()
    run(st, again, DAY, DAY, now=DAY_NOW, sample_every=8, themes=["inflation"])
    assert again.urls == []


def test_progress_is_reported_every_24_files_and_after_the_last(st):
    seen = []
    run(st, FakeGkg(), DAY, date(2025, 6, 5), now=utc(2025, 6, 6, 2), sample_every=8,
        progress=lambda **kw: seen.append(kw))
    assert [p["done"] for p in seen] == [24]
    assert seen[0]["phase"] == "files" and seen[0]["total"] == 24
    assert seen[0]["current"] == "2025-06-05T22:00:00Z"
    assert seen[0]["rows_written"] == len(gd.THEMES)       # the first day was flushed


# -- jobs and the ledger gate --------------------------------------------------------

def _submit(st, params):
    t = [utc(2026, 11, 6, 12)]

    def now():
        t[0] += timedelta(seconds=1)
        return t[0]

    class Launcher:
        mode = "container"

        def launch(self, job_id):
            return {"mode": "container"}

        def is_alive(self, job_id):
            return True

        def stop(self, job_id):
            pass
    return jobs.submit("ingest_gkg", params, store=st, launcher=Launcher(), now=now), now


def test_gkg_job_params_are_validated_with_defaults():
    base = {"start": "2025-06-01", "end": "2025-06-30"}
    out = jobs.validate_params("ingest_gkg", base)
    assert out["sample_every"] == 4 and out["themes"] == list(gd.THEMES)
    assert out["articles_start"] is None
    assert jobs.validate_params("ingest_gkg", dict(base, sample_every=8,
                                                   themes=["crypto"]))["sample_every"] == 8
    for bad, msg in ((dict(base, sample_every=3), "sample_every"),
                     (dict(base, sample_every=True), "sample_every"),
                     (dict(base, articles_start="2025-07-01"), "articles_start"),
                     (dict(base, themes=["nope"]), "theme"),
                     (dict(base, articles=False), "unknown param")):
        with pytest.raises(ValueError, match=msg):
            jobs.validate_params("ingest_gkg", bad)
    assert "ingest_gkg" in jobs.JOB_KINDS and "ingest_gkg" in jobs.RUNNERS


def test_gkg_job_runner_passes_params_and_reports_progress(st, monkeypatch):
    calls = []

    def fake(start, end, **kw):
        calls.append((start, end, kw))
        kw["progress"](phase="files", done=24, total=48, current="x", rows_written=27)
        return 27
    monkeypatch.setattr(gkg, "backfill_gkg", fake)
    job_id, now = _submit(st, {"start": "2025-06-01", "end": "2025-06-30",
                               "articles_start": "2025-06-10", "sample_every": 2,
                               "themes": ["crypto", "china"]})
    assert jobs.run(job_id, store=st, now=now) == "done"
    (start, end, kw), = calls
    assert (start, end) == (date(2025, 6, 1), date(2025, 6, 30))
    assert kw["articles_start"] == date(2025, 6, 10) and kw["sample_every"] == 2
    assert kw["themes"] == ["crypto", "china"] and kw["store"] is st
    assert isinstance(kw["now"], datetime) and kw["now"].tzinfo is not None
    p = st.get(jobs.TABLE, job_id)["progress"]
    assert (p["phase"], p["done"], p["total"], p["rows_written"]) == ("files", 24, 48, 27)


def test_gkg_job_honors_a_stop_at_its_next_progress(st, monkeypatch):
    def fake(start, end, **kw):
        st.update(jobs.TABLE, job_id, {"state": "stopped"})
        kw["progress"](phase="files", done=24, total=48, current="x", rows_written=0)
        raise AssertionError("ran past the stop")
    monkeypatch.setattr(gkg, "backfill_gkg", fake)
    job_id, now = _submit(st, {"start": "2025-06-01", "end": "2025-06-30"})
    assert jobs.run(job_id, store=st, now=now) == "stopped"


def test_ledger_refuses_to_start_while_an_ingest_gkg_job_runs(st):
    from trend_research import build_ledger as bl
    assert "ingest_gkg" in bl.INGEST_JOB_KINDS
    jid = "tr-job|2026-11-06T10:00:00.000000Z|ingest_gkg"
    st.insert(jobs.TABLE, {"id": jid, "kind": "ingest_gkg", "state": "running"})
    assert bl.active_ingest_jobs(store=st) == [jid]
    st.update(jobs.TABLE, jid, {"state": "done"})
    assert bl.active_ingest_jobs(store=st) == []
