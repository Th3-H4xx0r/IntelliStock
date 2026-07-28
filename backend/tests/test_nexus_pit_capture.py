from __future__ import annotations

from datetime import datetime

from strategies import graph_nexus_analysis as graph


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class _RecordingDriver:
    def export(self):
        return {
            "recording_version": 1,
            "queries": {
                "query-hash": {
                    "query": "MATCH (n) RETURN n",
                    "parameters": {},
                    "occurrences": [[{"n": "AAPL"}]],
                }
            },
        }


class _Registry:
    def __init__(self):
        self.call = None

    def finalize_bundle(self, **kwargs):
        self.call = kwargs
        return "manifest"


def test_capture_finalizes_all_required_sources(monkeypatch):
    registry = _Registry()
    monkeypatch.setattr(
        graph,
        "_neo4j_market_cap_cache",
        {"AAPL": 3_000_000_000_000},
    )
    monkeypatch.setattr(
        "ticker_universe.snapshot_current_universe",
        lambda: {
            "symbols": ["AAPL"],
            "rows": [
                {
                    "sym": "AAPL",
                    "price": 200.0,
                    "volume": 10_000_000,
                    "mcap": 3_000_000_000_000,
                }
            ],
        },
    )

    result = graph._capture_point_in_time_bundle(
        as_of=_ts("2026-07-28T20:00:00Z"),
        recording_driver=_RecordingDriver(),
        strategy_cache={
            "_yf_market_cap_cache": {"MSFT": 2_500_000_000_000}
        },
        alpaca_articles=[
            {
                "id": "alpaca-1",
                "created_at": "2026-07-28T19:59:00Z",
            }
        ],
        google_articles=[
            {
                "id": "google-1",
                "published_date": "2026-07-28T19:58:00Z",
            }
        ],
        benzinga_data={
            "ratings": [
                {
                    "ticker": "AAPL",
                    "published_at": "2026-07-28T19:57:00Z",
                }
            ]
        },
        registry=registry,
        code_revision="abc123",
    )

    assert result == "manifest"
    assert registry.call["code_revision"] == "abc123"
    assert set(registry.call["datasets"]) == {
        "graph",
        "fundamentals",
        "universe",
        "news",
    }
    assert registry.call["datasets"]["fundamentals"] == {
        "AAPL": {"market_cap": 3_000_000_000_000.0},
        "MSFT": {"market_cap": 2_500_000_000_000.0},
    }
    assert registry.call["datasets"]["news"]["alpaca"][0]["id"] == "alpaca-1"


def test_capture_failure_never_escapes_into_trading(monkeypatch):
    def fail_capture(**kwargs):
        raise RuntimeError("provider response contained sensitive details")

    logs = []
    monkeypatch.setattr(graph, "_capture_point_in_time_bundle", fail_capture)
    monkeypatch.setattr(
        graph,
        "_log",
        lambda message, color="white": logs.append(str(message)),
    )

    result = graph._finalize_pit_capture_safely(
        enabled=True,
        as_of=_ts("2026-07-28T20:00:00Z"),
        recording_driver=object(),
        strategy_cache={},
        alpaca_articles=[],
        google_articles=[],
        benzinga_data={},
    )

    assert result is None
    assert "RuntimeError" in logs[-1]
    assert "sensitive details" not in logs[-1]
