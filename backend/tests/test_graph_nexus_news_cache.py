"""1-C (bug-sweep 2026-05-28): live runs must not adopt a backtest's date-keyed
article/sentiment cache for the launch date (which skips the LLM). force_fresh
bypasses the cache hit so a fresh Alpaca fetch + LLM recompute happen.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _patch_news(monkeypatch, cached, sentiment, fresh):
    import strategies.graph_nexus_analysis as g
    monkeypatch.setattr(g, "_get_nexus_db_conn", lambda: object())
    monkeypatch.setattr(g, "_ensure_nexus_cache_table", lambda conn: None)
    monkeypatch.setattr(
        g, "_get_cached_articles",
        lambda conn, dk, sentiment_cache_scope_id="": (cached, sentiment),
    )
    monkeypatch.setattr(g, "_fetch_alpaca_news_all", lambda *a, **k: list(fresh))
    monkeypatch.setattr(g, "_filter_low_signal_alpaca_articles", lambda arts, **k: arts)
    monkeypatch.setattr(g, "_save_cached_articles", lambda *a, **k: None)
    return g


def test_cache_hit_returns_cached_articles_and_sentiment(monkeypatch):
    """Default (force_fresh=False): a sufficient cache returns cached articles +
    cached sentiment (the path that would skip the LLM)."""
    dt = datetime(2026, 5, 28, tzinfo=timezone.utc)
    g = _patch_news(monkeypatch, ["c1", "c2", "c3"], {"AAPL": 0.5},
                    [{"headline": "fresh", "created_at": "2026-05-28T08:00:00Z"}])
    arts, from_cache, sent = g._fetch_articles_cached(
        "2026-05-28", dt, dt, "k", "s", limit=30, min_articles=2,
    )
    assert from_cache is True
    assert sent == {"AAPL": 0.5}


def test_force_fresh_bypasses_cache_and_nulls_sentiment(monkeypatch):
    """force_fresh=True: cache hit ignored, fresh fetch performed, cached_sentiment
    comes back None so the caller re-runs the LLM on fresh articles."""
    dt = datetime(2026, 5, 28, tzinfo=timezone.utc)
    g = _patch_news(monkeypatch, ["c1", "c2", "c3"], {"AAPL": 0.5},
                    [{"headline": "fresh-overnight", "created_at": "2026-05-28T08:00:00Z"}])
    arts, from_cache, sent = g._fetch_articles_cached(
        "2026-05-28", dt, dt, "k", "s", limit=30, min_articles=2, force_fresh=True,
    )
    assert from_cache is False
    assert sent is None
    assert arts and arts[0]["headline"] == "fresh-overnight"
