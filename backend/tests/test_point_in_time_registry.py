from __future__ import annotations

from datetime import datetime

import pytest

from point_in_time_data import PointInTimeDataError, load_snapshot_payload
from point_in_time_registry import (
    InMemoryPointInTimeRegistry,
    content_hash,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _datasets() -> dict:
    return {
        "graph": {"recording_version": 1, "queries": []},
        "fundamentals": {
            "AAPL": {"market_cap": 1_000_000_000_000},
        },
        "universe": {
            "rows": [
                {
                    "sym": "AAPL",
                    "price": 200.0,
                    "volume": 10_000_000,
                    "mcap": 3_000_000_000_000,
                }
            ]
        },
        "news": {
            "alpaca": [
                {
                    "id": "known-at-cutoff",
                    "created_at": "2026-07-28T19:59:00Z",
                    "headline": "Known news",
                }
            ],
            "google": [],
            "benzinga": {},
        },
    }


def test_manifest_is_content_addressed_and_idempotent():
    registry = InMemoryPointInTimeRegistry()

    first = registry.finalize_bundle(
        as_of=_ts("2026-07-28T20:00:00Z"),
        datasets=_datasets(),
        code_revision="abc123",
    )
    second = registry.finalize_bundle(
        as_of=_ts("2026-07-28T20:00:00Z"),
        datasets=_datasets(),
        code_revision="abc123",
    )

    assert first == second
    assert first.status == "finalized"
    assert first.provenance == "strict_verified"
    assert first.manifest_id.startswith("pit-")
    assert set(first.source_hashes) == {
        "graph",
        "fundamentals",
        "universe",
        "news",
    }


def test_finalize_rejects_missing_required_dataset():
    datasets = _datasets()
    datasets.pop("news")

    with pytest.raises(PointInTimeDataError, match=r"missing.*news"):
        InMemoryPointInTimeRegistry().finalize_bundle(
            as_of=_ts("2026-07-28T20:00:00Z"),
            datasets=datasets,
            code_revision="abc123",
        )


def test_manifest_resolution_never_uses_a_future_cutoff():
    registry = InMemoryPointInTimeRegistry()
    registry.finalize_bundle(
        as_of=_ts("2026-07-29T20:00:00Z"),
        datasets=_datasets(),
        code_revision="abc123",
    )

    with pytest.raises(PointInTimeDataError, match="no finalized"):
        registry.resolve_bundle(_ts("2026-07-28T20:00:00Z"))


def test_manifest_resolution_selects_latest_eligible_cutoff():
    registry = InMemoryPointInTimeRegistry()
    early = registry.finalize_bundle(
        as_of=_ts("2026-07-28T19:00:00Z"),
        datasets=_datasets(),
        code_revision="abc123",
    )
    late_datasets = _datasets()
    late_datasets["fundamentals"]["AAPL"]["market_cap"] = 2_000_000_000_000
    late = registry.finalize_bundle(
        as_of=_ts("2026-07-28T20:00:00Z"),
        datasets=late_datasets,
        code_revision="abc123",
    )

    at_early = registry.resolve_bundle(_ts("2026-07-28T19:30:00Z"))
    at_late = registry.resolve_bundle(_ts("2026-07-28T20:30:00Z"))

    assert at_early.record.manifest_id == early.manifest_id
    assert at_late.record.manifest_id == late.manifest_id
    assert at_late.record.provenance == "strict_verified"
    assert load_snapshot_payload(
        at_late.store,
        dataset="fundamentals",
        context=at_late.context,
    )["AAPL"]["market_cap"] == 2_000_000_000_000


def test_snapshot_hash_is_verified_again_when_payload_is_loaded():
    registry = InMemoryPointInTimeRegistry()
    manifest = registry.finalize_bundle(
        as_of=_ts("2026-07-28T20:00:00Z"),
        datasets=_datasets(),
        code_revision="abc123",
    )
    graph_hash = manifest.source_hashes["graph"]
    registry._snapshots[graph_hash]["payload"] = {"tampered": True}

    bundle = registry.resolve_bundle(_ts("2026-07-28T20:00:00Z"))
    with pytest.raises(PointInTimeDataError, match="hash mismatch"):
        load_snapshot_payload(
            bundle.store,
            dataset="graph",
            context=bundle.context,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "secret"},
        {"nested": {"password": "secret"}},
        {"items": [{"access_token": "secret"}]},
    ],
)
def test_snapshot_payload_rejects_secret_shaped_keys(payload):
    datasets = _datasets()
    datasets["news"] = payload

    with pytest.raises(PointInTimeDataError, match="secret-bearing key"):
        InMemoryPointInTimeRegistry().finalize_bundle(
            as_of=_ts("2026-07-28T20:00:00Z"),
            datasets=datasets,
            code_revision="abc123",
        )


def test_content_hash_is_mapping_order_invariant():
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})
