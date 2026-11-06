"""TR market tape and weekly digest (plan Task 5).

No network: bars come from an injected loader, everything else from the
FakeStore (or PG_TEST_DSN). The decision under test is Monday 2026-01-12,
whose digest window is (2026-01-05 14:00Z, 2026-01-12 14:00Z] -- 09:00 ET on
each week's first session -- intersected with ``available_at < decision``.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from trend_research import digest as dg  # noqa: E402
from trend_research import market_tape as mt  # noqa: E402
from trend_research import pit_store  # noqa: E402

WINDOW_START = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 1, 12, 14, 0, tzinfo=timezone.utc)
DECISION = datetime(2026, 1, 12, 14, 30, tzinfo=timezone.utc)     # 09:30 ET


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def alpaca(at: datetime, sid: str, headline: str, symbols=(), summary="") -> dict:
    a = iso(at)
    return {"id": "%s|alpaca|%s" % (a, sid), "source": "alpaca", "source_id": sid,
            "published_at": a, "available_at": a, "headline": headline,
            "summary": summary, "content": "", "symbols": list(symbols),
            "url": "https://example.com/%s" % sid}


def gdelt(at: datetime, sid: str, headline: str, theme: str) -> dict:
    a = iso(at)
    return {"id": "%s|gdelt|%s" % (a, sid), "source": "gdelt", "source_id": sid,
            "published_at": a, "available_at": a, "headline": headline,
            "url": "https://news.example/%s" % sid, "domain": "news.example",
            "theme": theme, "symbols": []}


def sessions_before(end: date, n: int) -> list:
    """The ``n`` weekdays before ``end``, oldest first."""
    out, d = [], end
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out[::-1]


def bars(days, start=100.0, step=0.5) -> list:
    return [{"t": "%sT05:00:00Z" % d.isoformat(), "o": 1, "h": 1, "l": 1,
             "c": start + step * i, "v": 1000} for i, d in enumerate(days)]


def loader_for(by_symbol: dict, calls=None):
    def load(symbols, start, end):
        if calls is not None:
            calls.append((tuple(symbols), start, end))
        return {s: list(by_symbol[s]) for s in symbols if s in by_symbol}
    return load


def seed(st, rows_by_table: dict, *, shuffle_seed=None):
    for table, rows in rows_by_table.items():
        rows = list(rows)
        if shuffle_seed is not None:
            random.Random(shuffle_seed).shuffle(rows)
        if table in pit_store.PIT_TABLES:
            for row in rows:                       # one at a time: insert order matters
                pit_store.put_rows(table, [row], store=st)
        else:
            st.insert(table, rows)


def fixture_rows() -> dict:
    t = WINDOW_START + timedelta(hours=1)
    news = [
        alpaca(t, "a1", "Nvidia beats estimates, raises guidance!", ["NVDA"]),
        alpaca(t + timedelta(hours=1), "a2", "NVIDIA beats estimates; raises guidance",
               ["NVDA", "AMD"]),
        alpaca(t + timedelta(hours=2), "a3", "Oil slides as OPEC talks stall", ["USO"]),
        alpaca(t + timedelta(hours=3), "a4", "Fed minutes signal patience", []),
        gdelt(WINDOW_END, "g1", "Inflation cools in December", "inflation"),
        gdelt(WINDOW_END, "g2", "Prices rise again at the pump", "inflation"),
        gdelt(WINDOW_END, "g3", "Tariff talks resume", "tariffs"),
    ]
    themes = []
    for k in range(40):
        d = date(2026, 1, 11) - timedelta(days=k)
        avail = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1, hours=1)
        themes.append({"id": "%s|inflation" % d.isoformat(), "date": d.isoformat(),
                       "theme": "inflation", "volume": 100 + k, "total_articles": 10000,
                       "share": (100 + k) / 10000, "tone": -1.5,
                       "available_at": iso(avail)})
    macro = [
        {"id": "UNRATE|2025-11-01|2025-12-05", "series": "UNRATE", "obs_date": "2025-11-01",
         "vintage_date": "2025-12-05", "value": 4.4, "available_at": "2025-12-05T13:30:00Z"},
        {"id": "UNRATE|2025-12-01|2026-01-09", "series": "UNRATE", "obs_date": "2025-12-01",
         "vintage_date": "2026-01-09", "value": 4.6, "available_at": "2026-01-09T13:30:00Z"},
    ]
    vix = [{"id": "VIX|2026-01-09", "date": "2026-01-09", "close": 16.25}]
    return {"TrendNewsPIT": news, "TrendThemesPIT": themes, "TrendMacroPIT": macro,
            "SwingMacroDaily": vix}


def tape_bars() -> dict:
    days = sessions_before(date(2026, 1, 13), 260)       # includes the decision session
    return {s: bars(days, start=50.0 + i) for i, s in enumerate(mt.TAPE_SYMBOLS)}


# -- the four plan tests -------------------------------------------------------------

def test_digest_deterministic():
    loader = loader_for(tape_bars())
    st1, st2 = FakeStore(), FakeStore()
    seed(st1, fixture_rows())
    seed(st2, fixture_rows(), shuffle_seed=7)
    d1 = dg.weekly_digest(DECISION, store=st1, bars_loader=loader)
    d1_again = dg.weekly_digest(DECISION, store=st1, bars_loader=loader)
    d2 = dg.weekly_digest(DECISION, store=st2, bars_loader=loader)
    assert dg.digest_hash(d1) == dg.digest_hash(d1_again) == dg.digest_hash(d2)
    assert len(dg.digest_hash(d1)) == 64
    assert set(d1) >= {"headlines", "themes", "macro", "calendar", "tape"}
    # the hash is over content: a changed headline changes it
    d3 = dict(d1, headlines=d1["headlines"][:-1])
    assert dg.digest_hash(d3) != dg.digest_hash(d1)


def test_digest_excludes_future(store):
    decision = WINDOW_END                       # a decision exactly at the window end
    seed(store, {"TrendNewsPIT": [
        alpaca(decision - timedelta(seconds=1), "ok", "Visible one second before"),
        alpaca(decision, "at", "Stamped exactly at the decision"),
        alpaca(decision + timedelta(minutes=5), "late", "After the decision"),
        gdelt(decision, "gat", "GDELT title closing at the decision", "inflation"),
    ]})
    d = dg.weekly_digest(decision, store=store, bars_loader=loader_for({}))
    ids = {h["id"] for h in d["headlines"]}
    assert ids == {iso(decision - timedelta(seconds=1)) + "|alpaca|ok"}
    assert all(not th["titles"] for th in d["themes"])


def test_tape_uses_prior_sessions_only():
    days = sessions_before(date(2026, 1, 13), 260)        # last one is 2026-01-12
    assert days[-1] == date(2026, 1, 12)
    spy = bars(days)
    spy[-1]["c"] = 99999.0                                # the decision session's close
    calls = []
    tape = mt.market_tape(DECISION, bars_loader=loader_for({"SPY": spy}, calls),
                          store=FakeStore())
    s = tape["symbols"]["SPY"]
    assert s["last_session"] == "2026-01-09"
    assert "last_close" not in s                          # no price level (ruling R28)
    assert s["ret_1w"] == pytest.approx(spy[-2]["c"] / spy[-7]["c"] - 1, abs=1e-6)
    assert all(end < date(2026, 1, 12) for _syms, _start, end in calls)
    assert tape["symbols"]["QQQ"] is None                 # no bars for it
    assert tape["vix"] is None                            # no VIX row in the store


def test_symbol_stats_none_when_short_history():
    end = date(2026, 1, 12)
    short = bars(sessions_before(end, 59))
    enough = bars(sessions_before(end, 60))
    assert mt.symbol_trend_stats("ABC", DECISION, bars_loader=loader_for({"ABC": short})) is None
    stats = mt.symbol_trend_stats("abc", DECISION, bars_loader=loader_for({"ABC": enough}))
    assert stats is not None and stats["sessions"] == 60
    assert stats["sma200_dist"] is None                   # too short for SMA200
    assert stats["sma50_dist"] is not None


# -- tape details ---------------------------------------------------------------------

def test_tape_stats_values_and_vix():
    days = sessions_before(date(2026, 1, 12), 260)
    closes = [100.0 + i for i in range(260)]
    closes[-30] = 500.0                                   # a spike inside 3 months
    raw = [{"t": "%sT05:00:00Z" % d.isoformat(), "c": c} for d, c in zip(days, closes)]
    st = FakeStore()
    st.insert("SwingMacroDaily", [{"id": "VIX|2026-01-09", "date": "2026-01-09", "close": 16.25},
                                  {"id": "VIX|2026-01-12", "date": "2026-01-12", "close": 40.0}])
    tape = mt.market_tape(DECISION, bars_loader=loader_for({"XLK": raw}), store=st)
    assert tape["vix"] == 16.25                           # the decision day's row is unseen
    x = tape["symbols"]["XLK"]
    last = closes[-1]
    assert x["ret_1m"] == pytest.approx(last / closes[-22] - 1, abs=1e-6)
    assert x["ret_3m"] == pytest.approx(last / closes[-64] - 1, abs=1e-6)
    assert x["sma50_dist"] == pytest.approx(last / (sum(closes[-50:]) / 50) - 1, abs=1e-6)
    assert x["sma200_dist"] == pytest.approx(last / (sum(closes[-200:]) / 200) - 1, abs=1e-6)
    stats = mt.symbol_trend_stats("XLK", DECISION, bars_loader=loader_for({"XLK": raw}))
    assert stats["high_52w_dist"] == pytest.approx(last / 500.0 - 1, abs=1e-6)
    assert stats["max_drawdown_3m"] == pytest.approx(closes[-29] / 500.0 - 1, abs=1e-6)
    assert set(mt.TAPE_SYMBOLS) >= {"SPY", "QQQ", "IWM", "XLK", "XLRE", "XLC", "GLD",
                                     "SLV", "USO", "CPER", "TLT", "UUP"}
    assert len(mt.SECTOR_SPDRS) == 11


def test_default_loader_reads_swing_cache():
    st = FakeStore()
    days = sessions_before(date(2026, 1, 12), 80)
    by_year = {}
    for b in bars(days):
        by_year.setdefault(b["t"][:4], []).append(b)
    st.insert("SwingDailyBars", [{"id": "SPY|%s" % y, "feed": "iex", "adjustment": "split",
                                  "bars": bs} for y, bs in by_year.items()])
    stats = mt.symbol_trend_stats("SPY", DECISION, store=st)
    assert stats is not None and stats["sessions"] == 80


# -- digest details --------------------------------------------------------------------

def test_headline_clusters_scored_and_represented_by_earliest():
    t = WINDOW_START + timedelta(hours=2)
    seed_rows = [
        alpaca(t + timedelta(minutes=5), "b2", "Apple unveils new chip!!", ["AAPL"]),
        alpaca(t, "b1", "apple unveils new chip", ["AAPL", "TSM"]),
        alpaca(t + timedelta(minutes=9), "c1", "Solo story with three tickers", ["X", "Y", "Z"]),
        alpaca(t + timedelta(minutes=1), "d1", "Quiet note", []),
        alpaca(t + timedelta(minutes=2), "e1", "Another quiet note", []),
    ]
    st = FakeStore()
    seed(st, {"TrendNewsPIT": seed_rows})
    d = dg.weekly_digest(DECISION, store=st, bars_loader=loader_for({}), max_headlines=3)
    got = [h["id"].split("|")[-1] for h in d["headlines"]]
    # apple cluster: 2 x (1 + 2) = 6; solo: 1 x 4 = 4; quiet notes tie at 1 -> earliest
    assert got == ["b1", "c1", "d1"]
    assert d["headlines"][0] == {"id": iso(t) + "|alpaca|b1", "t": iso(t),
                                 "headline": "apple unveils new chip",
                                 "symbols": ["AAPL", "TSM"]}


def test_window_bounds_tile_weeks(store):
    seed(store, {"TrendNewsPIT": [
        alpaca(WINDOW_START, "start", "Stamped at the previous decision"),
        alpaca(WINDOW_START + timedelta(seconds=1), "after", "Just after it"),
        alpaca(WINDOW_END, "end", "Stamped at this window's end"),
        alpaca(WINDOW_END + timedelta(minutes=10), "next", "Belongs to next week"),
    ]})
    d = dg.weekly_digest(DECISION, store=store, bars_loader=loader_for({}))
    assert {h["id"].split("|")[-1] for h in d["headlines"]} == {"after", "end"}
    nxt = dg.weekly_digest(DECISION + timedelta(days=7), store=store, bars_loader=loader_for({}))
    assert {h["id"].split("|")[-1] for h in nxt["headlines"]} == {"next"}


def test_edited_article_keeps_earliest_visible_version(store):
    t = WINDOW_START + timedelta(hours=3)
    seed(store, {"TrendNewsPIT": [
        alpaca(t, "e1", "Original headline"),
        alpaca(t + timedelta(hours=2), "e1", "Edited headline"),
        # first seen last week, edited this week: not news this week
        alpaca(WINDOW_START - timedelta(days=2), "old", "Old story"),
        alpaca(t + timedelta(hours=1), "old", "Old story, updated"),
        # first version visible, its edit lands after the decision
        alpaca(t + timedelta(hours=4), "f1", "Before the edit"),
        alpaca(DECISION + timedelta(minutes=1), "f1", "After the edit"),
    ]})
    d = dg.weekly_digest(DECISION, store=store, bars_loader=loader_for({}))
    assert sorted((h["id"].split("|")[-1], h["headline"]) for h in d["headlines"]) == [
        ("e1", "Original headline"), ("f1", "Before the edit")]


def test_themes_macro_calendar_sections():
    st = FakeStore()
    seed(st, fixture_rows())
    d = dg.weekly_digest(DECISION, store=st, bars_loader=loader_for({}))
    themes = {th["theme"]: th for th in d["themes"]}
    assert set(themes) == {"inflation", "tariffs"}
    infl = themes["inflation"]
    assert [x["headline"] for x in infl["titles"]] == ["Inflation cools in December",
                                                        "Prices rise again at the pump"]
    assert infl["days"] == 7
    # this week's theme-days are dated 01-05..01-11 (volumes 100..106)
    assert infl["volume"] == sum(100 + k for k in range(7))
    assert infl["baseline_share"] is not None and infl["share_change"] < 0
    assert themes["tariffs"]["volume"] is None and len(themes["tariffs"]["titles"]) == 1
    macro = {m["series"]: m for m in d["macro"]}
    assert macro["UNRATE"]["value"] == 4.6 and macro["UNRATE"]["prev_value"] == 4.4
    assert macro["UNRATE"]["new_this_week"] is True
    assert any(e["kind"] for e in d["calendar"])
    assert all(datetime.fromisoformat(e["available_at"].replace("Z", "+00:00")) < DECISION
               for e in d["calendar"])
    assert d["tape"]["vix"] == 16.25


# -- symbol_news -------------------------------------------------------------------------

def test_symbol_news_filters_symbol_source_and_time(store):
    t = DECISION - timedelta(days=3)
    rows = [alpaca(t + timedelta(minutes=i), "n%d" % i, "NVDA story %d" % i, ["NVDA"])
            for i in range(5)]
    rows += [
        alpaca(t, "amd", "AMD only", ["AMD"]),
        alpaca(DECISION, "at", "NVDA at the decision", ["NVDA"]),
        alpaca(DECISION - timedelta(weeks=9), "old", "NVDA long ago", ["NVDA"]),
        gdelt(t, "g", "Nvidia in the world press", "ai_semiconductors"),
    ]
    seed(store, {"TrendNewsPIT": rows})
    got = dg.symbol_news("nvda", DECISION, store=store, limit=3)
    assert [r["id"].split("|")[-1] for r in got] == ["n4", "n3", "n2"]   # newest first
    assert all(r["source"] == "alpaca" for r in got)
    everything = dg.symbol_news("NVDA", DECISION, store=store)
    assert {r["id"].split("|")[-1] for r in everything} == {"n0", "n1", "n2", "n3", "n4"}
    preread = dg.visible_news(DECISION - timedelta(weeks=8), DECISION, store=store)
    assert dg.symbol_news("NVDA", DECISION, articles=preread) == everything
