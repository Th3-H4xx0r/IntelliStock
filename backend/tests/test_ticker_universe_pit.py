from __future__ import annotations

from datetime import datetime

import pytest

from point_in_time_data import (
    DatasetManifest,
    PointInTimeContext,
    PointInTimeDataError,
)
import ticker_universe


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        manifest_id="universe-manifest",
        source_hashes={"universe": "sha256:universe"},
        created_at=_ts("2026-03-03T00:00:00Z"),
    )


def _historical_context() -> PointInTimeContext:
    return PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )


def _store() -> dict:
    return {
        "universe": [
            {
                "manifest_id": "universe-manifest",
                "effective_at": _ts("2026-03-01T00:00:00Z"),
                "available_at": _ts("2026-03-01T01:00:00Z"),
                "payload": {
                    "symbols": ["OLDX", "LIQD"],
                    "rows": [
                        {
                            "sym": "LIQD",
                            "price": 25.0,
                            "volume": 1_000_000,
                            "mcap": 5_000_000_000,
                        },
                        {
                            "sym": "OLDX",
                            "price": 10.0,
                            "volume": 800_000,
                            "mcap": 3_000_000_000,
                        },
                    ],
                },
            }
        ]
    }


def test_historical_breadth_universe_never_fetches_current_listings(monkeypatch):
    monkeypatch.setattr(
        ticker_universe,
        "_fetch_universe_meta",
        lambda: pytest.fail("historical selection must not fetch current listings"),
    )

    symbols = ticker_universe.get_breadth_universe(
        min_mcap=2_000_000_000,
        min_dollar_volume=5_000_000,
        price_floor=5.0,
        top_n=10,
        context=_historical_context(),
        snapshot_store=_store(),
    )

    assert symbols == ["LIQD", "OLDX"]


def test_historical_ticker_validation_uses_dated_membership(monkeypatch):
    monkeypatch.setattr(ticker_universe, "_UNIVERSE", {"NEWI"})

    assert ticker_universe.is_valid_us_ticker(
        "OLDX",
        context=_historical_context(),
        snapshot_store=_store(),
    )
    assert not ticker_universe.is_valid_us_ticker(
        "NEWI",
        context=_historical_context(),
        snapshot_store=_store(),
    )


def test_historical_ticker_validation_accepts_dot_and_dash_share_classes():
    store = _store()
    store["universe"][0]["payload"]["rows"].append(
        {
            "sym": "BRK.B",
            "price": 500.0,
            "volume": 1_000_000,
            "mcap": 1_000_000_000_000,
        }
    )

    assert ticker_universe.is_valid_us_ticker(
        "BRK.B",
        context=_historical_context(),
        snapshot_store=store,
    )
    assert ticker_universe.is_valid_us_ticker(
        "BRK-B",
        context=_historical_context(),
        snapshot_store=store,
    )


def test_missing_historical_universe_snapshot_fails_closed():
    with pytest.raises(PointInTimeDataError, match="universe snapshot"):
        ticker_universe.get_breadth_universe(
            context=_historical_context(),
            snapshot_store={},
        )


def test_live_context_explicitly_uses_current_universe(monkeypatch):
    context = PointInTimeContext.for_live(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )
    monkeypatch.setattr(
        ticker_universe,
        "_fetch_universe_meta",
        lambda: [
            {
                "sym": "CURR",
                "price": 25.0,
                "volume": 1_000_000,
                "mcap": 5_000_000_000,
            }
        ],
    )
    monkeypatch.setattr(ticker_universe, "_BREADTH_CACHE", None)
    monkeypatch.setattr(ticker_universe, "_BREADTH_CACHE_KEY", None)
    monkeypatch.setattr(ticker_universe, "_BREADTH_LAST_FAIL_AT", 0.0)

    symbols = ticker_universe.get_breadth_universe(
        context=context,
        snapshot_store={"universe": {"current": ["IGNORED"]}},
    )

    assert symbols == ["CURR"]
