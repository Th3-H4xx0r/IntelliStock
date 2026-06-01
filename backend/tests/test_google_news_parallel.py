"""Live macro Google-News fetch must run its RSS pulls CONCURRENTLY.

Bug (2026-06-01, instance main): fetch_google_news / fetch_google_news_by_topic
looped serially over ~145 keywords+topics with a 1s sleep between each (~205s),
so the live open-cycle macro fetch always blew the 120s budget and the day's
macro signal was silently skipped. Fix = parallelize the fetches (bounded pool),
keeping the same merged/deduped/date-filtered output.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


def _make_article(url, pub=None):
    return {"url": url, "title": "t", "headline": "t",
            "published_date": pub, "description": "", "source": "x"}


def test_fetch_google_news_runs_concurrently(monkeypatch):
    import strategies.google_news as gn
    state = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def fake_fetch(url):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.1)
        with lock:
            state["cur"] -= 1
        return [_make_article(url)]

    monkeypatch.setattr(gn, "_fetch_rss_feed", fake_fetch)
    arts = gn.fetch_google_news(keywords=[f"k{i}" for i in range(8)], max_total=200)
    # A serial implementation would never have >1 fetch in flight.
    assert state["max"] > 1, "RSS keyword fetches must run concurrently"
    assert len(arts) == 8  # one unique article per keyword


def test_fetch_google_news_by_topic_runs_concurrently(monkeypatch):
    import strategies.google_news as gn
    state = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def fake_fetch(url):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.1)
        with lock:
            state["cur"] -= 1
        return [_make_article(url)]

    monkeypatch.setattr(gn, "_fetch_rss_feed", fake_fetch)
    gn.fetch_google_news_by_topic(topics=None)  # DEFAULT_TOPICS (6)
    assert state["max"] > 1, "topic RSS fetches must run concurrently"


def test_fetch_google_news_deterministic_under_out_of_order_completion(monkeypatch):
    """Backtest reproducibility: thread COMPLETION order must not leak into output.
    k0 completes LAST but its article must still come first (keyword order)."""
    import strategies.google_news as gn

    def fake_fetch(url):
        if "k0" in url:
            time.sleep(0.20)   # completes last
            return [_make_article("u0")]
        if "k1" in url:
            time.sleep(0.05)
            return [_make_article("u1")]
        time.sleep(0.01)       # k2 completes first
        return [_make_article("u2")]

    monkeypatch.setattr(gn, "_fetch_rss_feed", fake_fetch)
    arts = gn.fetch_google_news(keywords=["k0", "k1", "k2"], max_total=200)
    assert [a["url"] for a in arts] == ["u0", "u1", "u2"], \
        "output must follow keyword order, not thread-completion order"


def test_fetch_google_news_respects_max_total(monkeypatch):
    """The global max_total cap must stop appending mid-stream, in keyword order
    (the parallel merge preserves the serial cut)."""
    import strategies.google_news as gn

    def fake_fetch(url):
        if "k0" in url:
            return [_make_article("a"), _make_article("b")]
        if "k1" in url:
            return [_make_article("c"), _make_article("d")]
        if "k2" in url:
            return [_make_article("e"), _make_article("f")]
        return []

    monkeypatch.setattr(gn, "_fetch_rss_feed", fake_fetch)
    arts = gn.fetch_google_news(keywords=["k0", "k1", "k2"], max_total=3, max_results_per_keyword=30)
    assert [a["url"] for a in arts] == ["a", "b", "c"], "max_total=3 → first 3 in keyword order"


def test_fetch_google_news_dedups_and_date_filters(monkeypatch):
    """Parallel fetch must preserve the serial output contract: dedup by url +
    drop articles published after end_date (backtest/no-leakage safety)."""
    import strategies.google_news as gn
    end = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def fake_fetch(url):
        if "k0" in url:
            return [_make_article("u1", datetime(2026, 5, 30, tzinfo=timezone.utc))]
        if "k1" in url:
            return [_make_article("u1", datetime(2026, 5, 30, tzinfo=timezone.utc)),  # dup url
                    _make_article("u2", datetime(2026, 5, 31, tzinfo=timezone.utc))]
        if "k2" in url:
            return [_make_article("u3", datetime(2026, 6, 5, tzinfo=timezone.utc))]  # future → filtered
        return []

    monkeypatch.setattr(gn, "_fetch_rss_feed", fake_fetch)
    arts = gn.fetch_google_news(keywords=["k0", "k1", "k2"], end_date=end, max_total=200)
    urls = [a["url"] for a in arts]
    assert urls == ["u1", "u2"], f"expected dedup(u1)+date-filter(u3), got {urls}"
