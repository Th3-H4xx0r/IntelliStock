from __future__ import annotations

from datetime import datetime
import sys
import types

import pytest

from point_in_time_data import (
    DatasetManifest,
    PointInTimeContext,
    PointInTimeDataError,
)
from strategies import graph_nexus_analysis as graph


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _context(as_of: str) -> PointInTimeContext:
    return PointInTimeContext(
        as_of=_ts(as_of),
        manifest=DatasetManifest(
            manifest_id="news-manifest",
            source_hashes={"news": "sha256:news"},
            created_at=_ts("2026-03-03T00:00:00Z"),
        ),
    )


def _article(article_id: str, published_at: str) -> dict:
    return {
        "id": article_id,
        "headline": f"Market update {article_id}",
        "created_at": published_at,
        "symbols": ["AAPL"],
    }


def _news_store(*, alpaca=None, google=None, benzinga=None) -> dict:
    return {
        "news": [
            {
                "manifest_id": "news-manifest",
                "effective_at": _ts("2026-03-02T13:00:00Z"),
                "available_at": _ts("2026-03-02T13:00:00Z"),
                "payload": {
                    "alpaca": list(alpaca or []),
                    "google": list(google or []),
                    "benzinga": dict(benzinga or {}),
                },
            }
        ]
    }


def test_full_day_article_cache_is_filtered_before_sentiment_reuse(monkeypatch):
    context = _context("2026-03-02T14:00:00Z")
    cached = [
        _article("13:59", "2026-03-02T13:59:00Z"),
        _article("14:01", "2026-03-02T14:01:00Z"),
    ]
    monkeypatch.setattr(graph, "_get_nexus_db_conn", lambda: object())
    monkeypatch.setattr(graph, "_ensure_nexus_cache_table", lambda conn: None)
    monkeypatch.setattr(
        graph,
        "_get_cached_articles",
        lambda *args, **kwargs: pytest.fail(
            "strict history must not consult the mutable article cache"
        ),
    )
    monkeypatch.setattr(
        graph,
        "_fetch_alpaca_news_all",
        lambda *args, **kwargs: pytest.fail("sufficient cache must not refetch"),
    )

    articles, from_cache, sentiment = graph._fetch_articles_cached(
        "2026-03-02",
        _ts("2026-03-02T00:00:00Z"),
        _ts("2026-03-03T00:00:00Z"),
        "key",
        "secret",
        limit=50,
        min_articles=1,
        context=context,
        snapshot_store=_news_store(alpaca=cached),
    )

    assert [article["id"] for article in articles] == ["13:59"]
    assert from_cache is True
    assert sentiment is None


def test_sentiment_scope_changes_with_manifest_or_as_of():
    early = _context("2026-03-02T14:00:00Z")
    late = _context("2026-03-02T15:00:00Z")

    early_scope = graph._point_in_time_sentiment_scope_id("model-scope", early)
    late_scope = graph._point_in_time_sentiment_scope_id("model-scope", late)

    assert early_scope != late_scope
    assert early_scope == graph._point_in_time_sentiment_scope_id(
        "model-scope", early
    )


def test_saved_sentiment_is_partitioned_by_point_in_time_scope(monkeypatch):
    store = {
        "2026-03-02": {
            "id": "2026-03-02",
            "articles": [_article("13:59", "2026-03-02T13:59:00Z")],
        }
    }

    class _Op:
        def __init__(self, operation):
            self._operation = operation

        def run(self, conn):
            return self._operation()

    class _Row:
        def get(self, key):
            return _Op(lambda: store.get(key))

        def update(self, patch):
            def _update():
                store["2026-03-02"].update(patch)
                return {"replaced": 1}

            return _Op(_update)

    class _Table:
        def get(self, key):
            row = _Row()
            original_get = row.get

            class _BoundRow:
                def run(self, conn):
                    return original_get(key).run(conn)

                def update(self, patch):
                    return row.update(patch)

            return _BoundRow()

    class _Db:
        def table(self, name):
            return _Table()

    class _R:
        def db(self, name):
            return _Db()

        def now(self):
            return "now"

    context = _context("2026-03-02T14:00:00Z")
    monkeypatch.setattr(graph, "_r", _R())

    graph._save_cached_sentiment(
        object(),
        "2026-03-02",
        {"AAPL": {"sentiment": 1}},
        [_article("13:59", "2026-03-02T13:59:00Z")],
        sentiment_cache_scope_id="model-scope",
        context=context,
    )

    expected_scope = graph._point_in_time_sentiment_scope_id(
        "model-scope", context
    )
    assert set(store["2026-03-02"]["sentiment_by_scope"]) == {expected_scope}


def test_google_news_cache_is_filtered_at_the_same_boundary(monkeypatch):
    cached = [
        {
            "id": "13:59",
            "title": "Markets gain before policy decision",
            "published_date": "2026-03-02T13:59:00Z",
            "url": "https://example.invalid/early",
        },
        {
            "id": "14:01",
            "title": "Policy decision surprises markets",
            "published_date": "2026-03-02T14:01:00Z",
            "url": "https://example.invalid/future",
        },
    ]
    fake_google_news = types.SimpleNamespace(
        fetch_google_news=lambda **kwargs: [],
        fetch_google_news_by_topic=lambda **kwargs: [],
        load_cached_articles=lambda conn, date_key, keywords_hash: list(cached),
        cache_articles=lambda *args, **kwargs: None,
        compute_keywords_hash=lambda keywords, topics: "keywords-hash",
        DEFAULT_KEYWORDS=["markets"],
        DEFAULT_TOPICS=["BUSINESS"],
    )
    monkeypatch.setitem(sys.modules, "google_news", fake_google_news)

    articles = graph._fetch_google_news_cached(
        "2026-03-02",
        _ts("2026-03-02T00:00:00Z"),
        _ts("2026-03-03T00:00:00Z"),
        {"google_news_enabled": True},
        conn=object(),
        context=_context("2026-03-02T14:00:00Z"),
        snapshot_store=_news_store(google=cached),
    )

    assert [article["id"] for article in articles] == ["13:59"]


def test_strict_news_context_rejects_an_undated_cached_article(monkeypatch):
    monkeypatch.setattr(graph, "_get_nexus_db_conn", lambda: object())
    monkeypatch.setattr(graph, "_ensure_nexus_cache_table", lambda conn: None)
    monkeypatch.setattr(
        graph,
        "_get_cached_articles",
        lambda *args, **kwargs: pytest.fail(
            "strict history must not consult the mutable article cache"
        ),
    )

    with pytest.raises(PointInTimeDataError, match="availability timestamp"):
        graph._fetch_articles_cached(
            "2026-03-02",
            _ts("2026-03-02T00:00:00Z"),
            _ts("2026-03-03T00:00:00Z"),
            "key",
            "secret",
            limit=50,
            min_articles=1,
            context=_context("2026-03-02T14:00:00Z"),
            snapshot_store=_news_store(
                alpaca=[
                    {"id": "undated", "headline": "Undated market update"}
                ]
            ),
        )


def test_cached_article_reader_does_not_swallow_strict_timestamp_errors(
    monkeypatch,
):
    class _Query:
        def run(self, conn):
            return {
                "id": "2026-03-02",
                "articles": [
                    {"id": "undated", "headline": "Undated market update"}
                ],
            }

    class _Table:
        def get(self, key):
            return _Query()

    class _Db:
        def table(self, name):
            return _Table()

    class _R:
        def db(self, name):
            return _Db()

    monkeypatch.setattr(graph, "_r", _R())

    with pytest.raises(PointInTimeDataError, match="availability timestamp"):
        graph._get_cached_articles(
            object(),
            "2026-03-02",
            context=_context("2026-03-02T14:00:00Z"),
        )


def _install_cached_benzinga_module(monkeypatch):
    fake_benzinga = types.SimpleNamespace(
        fetch_benzinga_bulk=lambda *args, **kwargs: pytest.fail(
            "preloaded bulk cache must not make a provider call"
        ),
        _strip_future_actuals=lambda data_type, records, date_key: list(records),
        _FORWARD_LOOKING_TYPES=set(),
        set_benzinga_backtest_mode=lambda enabled: None,
    )
    monkeypatch.setitem(sys.modules, "benzinga_client", fake_benzinga)


def test_benzinga_cache_is_filtered_on_aware_publication_time(monkeypatch):
    _install_cached_benzinga_module(monkeypatch)
    cache = {
        "_bz_bulk_range": ("2026-02-01", "2026-04-01"),
        "_bz_bulk_data": {
            "ratings": [
                {
                    "ticker": "AAPL",
                    "date": "2026-03-02",
                    "published_at": "2026-03-02T13:59:00Z",
                },
                {
                    "ticker": "MSFT",
                    "date": "2026-03-02",
                    "published_at": "2026-03-02T14:01:00Z",
                },
            ]
        },
    }

    result = graph._fetch_all_benzinga(
        {},
        "2026-03-02",
        ["AAPL", "MSFT"],
        strategy_cache={},
        context=_context("2026-03-02T14:00:00Z"),
        snapshot_store=_news_store(
            benzinga=cache["_bz_bulk_data"],
        ),
    )

    assert [row["ticker"] for row in result["ratings"]] == ["AAPL"]


def test_benzinga_strict_history_rejects_undated_cached_records(monkeypatch):
    _install_cached_benzinga_module(monkeypatch)
    cache = {
        "_bz_bulk_range": ("2026-02-01", "2026-04-01"),
        "_bz_bulk_data": {
            "ratings": [{"ticker": "AAPL", "date": "2026-03-02"}],
        },
    }

    with pytest.raises(PointInTimeDataError, match="availability timestamp"):
        graph._fetch_all_benzinga(
            {},
            "2026-03-02",
            ["AAPL"],
            strategy_cache={},
            context=_context("2026-03-02T14:00:00Z"),
            snapshot_store=_news_store(
                benzinga=cache["_bz_bulk_data"],
            ),
        )
