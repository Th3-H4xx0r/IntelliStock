from __future__ import annotations

from datetime import datetime

import pytest

from point_in_time_data import (
    DatasetManifest,
    PointInTimeContext,
    PointInTimeDataError,
)
from strategies import graph_nexus_analysis as graph


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        manifest_id="pit-manifest",
        source_hashes={
            "fundamentals": "sha256:fundamentals",
            "graph": "sha256:graph",
        },
        created_at=_ts("2026-03-03T00:00:00Z"),
    )


def _historical_context() -> PointInTimeContext:
    return PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )


def test_missing_historical_graph_snapshot_fails_instead_of_using_current():
    store = {"graph": {"current": {"payload": "current-graph"}}}

    with pytest.raises(PointInTimeDataError, match="graph snapshot"):
        graph.load_graph_snapshot(
            context=_historical_context(),
            store=store,
        )


def test_historical_graph_snapshot_uses_latest_available_manifest_match():
    store = {
        "graph": [
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-02T12:00:00Z"),
                "available_at": _ts("2026-03-02T12:05:00Z"),
                "payload": "graph-12",
            },
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-02T13:00:00Z"),
                "available_at": _ts("2026-03-02T13:05:00Z"),
                "payload": "graph-13",
            },
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-02T13:30:00Z"),
                "available_at": _ts("2026-03-02T14:01:00Z"),
                "payload": "not-yet-published",
            },
        ]
    }

    snapshot = graph.load_graph_snapshot(
        context=_historical_context(),
        store=store,
    )

    assert snapshot == "graph-13"


def test_live_graph_context_explicitly_uses_current_snapshot():
    context = PointInTimeContext.for_live(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )
    current = object()

    snapshot = graph.load_graph_snapshot(
        context=context,
        store={"graph": {"current": current}},
    )

    assert snapshot is current


def test_historical_fundamentals_seed_never_uses_current_market_cap(monkeypatch):
    context = _historical_context()
    fundamentals_store = {
        "fundamentals": [
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-01T00:00:00Z"),
                "available_at": _ts("2026-03-01T01:00:00Z"),
                "payload": {
                    "AAPL": {"market_cap": 1_500_000_000_000},
                },
            }
        ]
    }
    cache: dict = {}
    monkeypatch.setitem(
        graph._neo4j_market_cap_cache,
        "AAPL",
        9_999_999_999_999,
    )

    populated = graph._preseed_mcap_cache_from_universe(
        ["AAPL"],
        cache,
        {"mcap_preseed_use_yfinance": False},
        context=context,
        fundamentals_store=fundamentals_store,
    )

    assert populated == 1
    assert cache["_yf_market_cap_cache"]["AAPL"] == 1_500_000_000_000


def test_missing_historical_fundamentals_snapshot_fails_closed():
    with pytest.raises(PointInTimeDataError, match="fundamentals snapshot"):
        graph._preseed_mcap_cache_from_universe(
            ["AAPL"],
            {},
            {"mcap_preseed_use_yfinance": False},
            context=_historical_context(),
            fundamentals_store={},
        )


def test_fundamentals_cache_is_scoped_by_manifest_and_as_of():
    early = PointInTimeContext(
        as_of=_ts("2026-03-02T12:30:00Z"),
        manifest=_manifest(),
    )
    late = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )
    store = {
        "fundamentals": [
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-02T12:00:00Z"),
                "available_at": _ts("2026-03-02T12:05:00Z"),
                "payload": {"AAPL": {"market_cap": 1_000_000_000_000}},
            },
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-02T13:00:00Z"),
                "available_at": _ts("2026-03-02T13:05:00Z"),
                "payload": {"AAPL": {"market_cap": 2_000_000_000_000}},
            },
        ]
    }
    cache: dict = {}

    graph._preseed_mcap_cache_from_universe(
        ["AAPL"],
        cache,
        {"mcap_preseed_use_yfinance": False},
        context=early,
        fundamentals_store=store,
    )
    graph._preseed_mcap_cache_from_universe(
        ["AAPL"],
        cache,
        {"mcap_preseed_use_yfinance": False},
        context=late,
        fundamentals_store=store,
    )

    assert cache["_yf_market_cap_cache"]["AAPL"] == 2_000_000_000_000


def test_graph_derived_caches_reset_when_point_in_time_scope_changes(
    monkeypatch,
):
    early = PointInTimeContext(
        as_of=_ts("2026-03-02T12:30:00Z"),
        manifest=_manifest(),
    )
    late = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )
    strategy_cache: dict = {}
    config: dict = {}
    monkeypatch.setattr(graph, "_active_point_in_time_graph_scope", None)

    graph._activate_point_in_time_graph_scope(
        early,
        strategy_cache=strategy_cache,
        config=config,
    )
    monkeypatch.setattr(graph, "_momentum_neighbor_cache", {"key": "early"})
    monkeypatch.setattr(
        graph,
        "_PRIVATE_ENTITY_ALIAS_QUERY_CACHE",
        {"early": [{"ticker": "AAPL"}]},
    )
    monkeypatch.setattr(
        graph,
        "_PRIVATE_ENTITY_ALIAS_QUERY_CACHE_ORDER",
        ["early"],
    )
    monkeypatch.setattr(graph, "_neo4j_etf_cache", {"early": ["SPY"]})
    monkeypatch.setattr(graph, "_neo4j_stock_sector_cache", {"AAPL": ["Tech"]})
    monkeypatch.setattr(graph, "_neo4j_market_cap_cache", {"AAPL": 1.0})
    strategy_cache["_neo4j_snapshot"] = {"early": ["cached edge"]}
    strategy_cache["_neo4j_snapshot_stats"] = {"hits": 1, "misses": 0}
    strategy_cache["_inst_co_holdings_cache"] = [{"ticker": "AAPL"}]
    config["_inst_co_holdings_cache"] = [{"ticker": "AAPL"}]

    graph._activate_point_in_time_graph_scope(
        late,
        strategy_cache=strategy_cache,
        config=config,
    )

    assert graph._momentum_neighbor_cache == {}
    assert graph._PRIVATE_ENTITY_ALIAS_QUERY_CACHE == {}
    assert graph._PRIVATE_ENTITY_ALIAS_QUERY_CACHE_ORDER == []
    assert graph._neo4j_etf_cache is None
    assert graph._neo4j_stock_sector_cache is None
    assert graph._neo4j_market_cap_cache == {}
    assert "_neo4j_snapshot" not in strategy_cache
    assert "_neo4j_snapshot_stats" not in strategy_cache
    assert "_inst_co_holdings_cache" not in strategy_cache
    assert "_inst_co_holdings_cache" not in config
    assert strategy_cache["_nexus_graph_pit_scope"] == late.cache_key(
        "nexus-graph"
    )


def test_historical_entrypoint_requires_a_point_in_time_context():
    strategy = graph.GraphNexusAnalysis()

    with pytest.raises(PointInTimeDataError, match="PointInTimeContext"):
        strategy.run_historical(
            [],
            {},
            _ts("2026-03-02T14:00:00Z"),
            {},
            {},
            context=None,
            point_in_time_store={},
            session_close_resolver=lambda session_date: _ts(
                f"{session_date.isoformat()}T21:00:00Z"
            ),
        )


def test_historical_entrypoint_preloads_snapshots_and_delegates(monkeypatch):
    strategy = graph.GraphNexusAnalysis()
    context = _historical_context()
    graph_driver = object()
    store = {
        "graph": [
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-01T00:00:00Z"),
                "available_at": _ts("2026-03-01T01:00:00Z"),
                "payload": graph_driver,
            }
        ],
        "fundamentals": [
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-01T00:00:00Z"),
                "available_at": _ts("2026-03-01T01:00:00Z"),
                "payload": {"AAPL": {"market_cap": 1_500_000_000_000}},
            }
        ],
        "universe": [
            {
                "manifest_id": "pit-manifest",
                "effective_at": _ts("2026-03-01T00:00:00Z"),
                "available_at": _ts("2026-03-01T01:00:00Z"),
                "payload": {"symbols": ["AAPL"]},
            }
        ],
    }
    captured = {}

    def _fake_run_once(*args, **kwargs):
        captured.update(kwargs)
        return {"AAPL": {"score": 0}}

    monkeypatch.setattr(strategy, "run_once", _fake_run_once)
    resolver = lambda session_date: _ts(
        f"{session_date.isoformat()}T21:00:00Z"
    )

    result = strategy.run_historical(
        ["AAPL"],
        {"AAPL": 100.0},
        context.as_of,
        {},
        {},
        data={"AAPL": []},
        strategy_cache={},
        context=context,
        point_in_time_store=store,
        session_close_resolver=resolver,
    )

    assert result == {"AAPL": {"score": 0}}
    assert captured["point_in_time_context"] is context
    assert captured["point_in_time_store"] is store
    assert captured["session_close_resolver"] is resolver
