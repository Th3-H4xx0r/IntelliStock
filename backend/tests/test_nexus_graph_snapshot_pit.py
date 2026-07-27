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
    assert type(captured["point_in_time_store"]).__name__ == (
        "ImmutableSnapshotStore"
    )
    assert captured["point_in_time_fundamentals"]["AAPL"]["market_cap"] == (
        1_500_000_000_000
    )
    assert captured["session_close_resolver"] is resolver


def test_historical_entrypoint_requires_strict_context():
    context = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
        strict=False,
        is_live=False,
    )

    with pytest.raises(PointInTimeDataError, match="strict"):
        graph.GraphNexusAnalysis().run_historical(
            [],
            {},
            context.as_of,
            {},
            {},
            context=context,
            point_in_time_store={},
            session_close_resolver=lambda session_date: _ts(
                f"{session_date.isoformat()}T21:00:00Z"
            ),
        )


def test_historical_run_once_rejects_missing_context_before_pipeline(
    monkeypatch,
):
    monkeypatch.setattr(
        graph,
        "_activate_point_in_time_graph_scope",
        lambda *args, **kwargs: pytest.fail(
            "historical context must be checked before pipeline activation"
        ),
    )

    with pytest.raises(PointInTimeDataError, match="historical.*context"):
        graph.GraphNexusAnalysis().run_once(
            [],
            {},
            _ts("2026-03-02T14:00:00Z"),
            {},
            {},
            data={},
            strategy_cache={},
        )


def test_historical_run_once_rejects_non_strict_context_before_pipeline(
    monkeypatch,
):
    context = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
        strict=False,
        is_live=False,
    )
    monkeypatch.setattr(
        graph,
        "_activate_point_in_time_graph_scope",
        lambda *args, **kwargs: pytest.fail(
            "strict context must be checked before pipeline activation"
        ),
    )

    with pytest.raises(PointInTimeDataError, match="strict"):
        graph.GraphNexusAnalysis().run_once(
            [],
            {},
            context.as_of,
            {},
            {},
            data={},
            strategy_cache={},
            point_in_time_context=context,
            point_in_time_store={},
            session_close_resolver=lambda session_date: _ts(
                f"{session_date.isoformat()}T21:00:00Z"
            ),
        )


def test_strict_market_cap_consumers_use_only_dated_fundamentals(monkeypatch):
    context = _historical_context()
    fundamentals = {"AAPL": {"market_cap": 1_500_000_000_000}}
    cache = {
        "_yf_market_cap_cache": {"AAPL": 9_999_999_999_999},
        "_ticker_metadata": {
            "AAPL": {"market_cap": 8_888_888_888_888},
        },
        "_symbol_quality_metadata": {
            "AAPL": {"market_cap": 7_777_777_777_777},
        },
    }
    monkeypatch.setitem(
        graph._neo4j_market_cap_cache,
        "AAPL",
        6_666_666_666_666,
    )

    assert graph._v32_get_market_cap(
        "AAPL",
        cache,
        {},
        context=context,
        fundamentals=fundamentals,
    ) == 1_500_000_000_000
    assert graph._resolve_position_market_cap(
        "AAPL",
        cache,
        context=context,
        fundamentals=fundamentals,
    ) == 1_500_000_000_000
    quality = graph._extract_quality_metadata(
        "AAPL",
        {"quality_metadata": {"market_cap": 5_555_555_555_555}},
        {"AAPL": 100.0},
        {},
        strategy_cache=cache,
        context=context,
        fundamentals=fundamentals,
    )
    assert quality["market_cap"] == 1_500_000_000_000
    assert "yfinance" not in quality["sources"]
    assert "neo4j" not in quality["sources"]


@pytest.mark.parametrize(
    "consumer",
    [
        lambda context: graph._v32_get_market_cap(
            "AAPL",
            {"_ticker_metadata": {"AAPL": {"market_cap": 9e12}}},
            {},
            context=context,
            fundamentals={},
        ),
        lambda context: graph._resolve_position_market_cap(
            "AAPL",
            {"_yf_market_cap_cache": {"AAPL": 9e12}},
            context=context,
            fundamentals={},
        ),
        lambda context: graph._extract_quality_metadata(
            "AAPL",
            {"quality_metadata": {"market_cap": 9e12}},
            {"AAPL": 100.0},
            {},
            strategy_cache={},
            context=context,
            fundamentals={},
        ),
    ],
)
def test_strict_market_cap_consumers_fail_closed_when_dated_value_missing(
    consumer,
):
    with pytest.raises(PointInTimeDataError, match="market_cap.*AAPL"):
        consumer(_historical_context())


def test_institutional_cache_binding_includes_manifest_and_exact_as_of():
    early = _historical_context()
    late = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:01Z"),
        manifest=_manifest(),
    )

    early_key, early_metadata = graph._institutional_cache_binding(
        early,
        "main-instance",
        "2026-03-02",
    )
    late_key, _ = graph._institutional_cache_binding(
        late,
        "main-instance",
        "2026-03-02",
    )

    assert "pit-manifest" in early_key
    assert "2026-03-02T14:00:00Z" in early_key
    assert early_key != late_key
    assert early_metadata == {
        "manifest_id": "pit-manifest",
        "as_of": "2026-03-02T14:00:00Z",
    }


def test_strict_institutional_cache_rejects_legacy_unscoped_row():
    context = _historical_context()
    legacy = {
        "id": "inst_co_holdings|main-instance|2026-03",
        "edges": [{"left": "AAPL", "right": "MSFT"}],
    }

    assert graph._institutional_cache_row_matches(legacy, context) is False
