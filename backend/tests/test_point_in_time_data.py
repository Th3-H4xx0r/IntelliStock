from __future__ import annotations

from datetime import datetime, timezone

import pytest

from point_in_time_data import (
    DatasetManifest,
    PointInTimeContext,
    PointInTimeDataError,
    filter_available,
)


UTC = timezone.utc


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        manifest_id="manifest-2026-03-02",
        source_hashes={"news": "sha256:news", "graph": "sha256:graph"},
        created_at=_ts("2026-03-03T00:00:00Z"),
    )


def test_strict_context_filters_records_available_after_as_of():
    context = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )
    records = (
        {"id": "13:59", "published_at": _ts("2026-03-02T13:59:00Z")},
        {"id": "14:01", "published_at": _ts("2026-03-02T14:01:00Z")},
    )

    available = filter_available(
        records,
        context=context,
        available_at=lambda record: record["published_at"],
    )

    assert tuple(record["id"] for record in available) == ("13:59",)


def test_strict_context_rejects_missing_availability_instead_of_guessing():
    context = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )

    with pytest.raises(PointInTimeDataError, match="availability timestamp"):
        filter_available(
            ({"id": "undated"},),
            context=context,
            available_at=lambda record: record.get("published_at"),
        )


def test_context_rejects_naive_as_of_timestamp():
    with pytest.raises(PointInTimeDataError, match="timezone-aware"):
        PointInTimeContext(
            as_of=datetime(2026, 3, 2, 14, 0),
            manifest=_manifest(),
        )


def test_manifest_source_hashes_are_immutable():
    manifest = _manifest()

    with pytest.raises(TypeError):
        manifest.source_hashes["news"] = "changed"  # type: ignore[index]

    assert dict(manifest.source_hashes) == {
        "graph": "sha256:graph",
        "news": "sha256:news",
    }


def test_derived_cache_key_is_bound_to_manifest_and_as_of():
    context = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )

    key = context.cache_key("sentiment", "AAPL")

    assert key == (
        "sentiment",
        "manifest-2026-03-02",
        "2026-03-02T14:00:00Z",
        "AAPL",
    )


def test_live_context_is_explicit_and_still_uses_aware_utc_time():
    context = PointInTimeContext.for_live(
        as_of=datetime(2026, 3, 2, 19, 0, tzinfo=UTC),
        manifest=_manifest(),
    )

    assert context.is_live is True
    assert context.strict is False
    assert context.as_of == _ts("2026-03-02T19:00:00Z")


def test_immutable_snapshot_store_copies_and_freezes_nested_payloads():
    from point_in_time_data import ImmutableSnapshotStore, load_snapshot_payload

    source = {
        "fundamentals": [
            {
                "manifest_id": _manifest().manifest_id,
                "effective_at": _ts("2026-03-02T12:00:00Z"),
                "available_at": _ts("2026-03-02T12:05:00Z"),
                "payload": {
                    "AAPL": {
                        "market_cap": 1_000_000_000_000,
                        "tags": ["mega-cap"],
                    }
                },
            }
        ]
    }
    store = ImmutableSnapshotStore(source)
    source["fundamentals"][0]["payload"]["AAPL"]["market_cap"] = 9
    source["fundamentals"][0]["payload"]["AAPL"]["tags"].append("mutated")
    context = PointInTimeContext(
        as_of=_ts("2026-03-02T14:00:00Z"),
        manifest=_manifest(),
    )

    payload = load_snapshot_payload(
        store,
        dataset="fundamentals",
        context=context,
    )

    assert payload["AAPL"]["market_cap"] == 1_000_000_000_000
    assert payload["AAPL"]["tags"] == ("mega-cap",)
    with pytest.raises(TypeError):
        payload["AAPL"]["market_cap"] = 9
