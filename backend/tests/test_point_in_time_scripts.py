from __future__ import annotations

from datetime import date

import pytest

from point_in_time_data import PointInTimeDataError
from scripts.audit_point_in_time_coverage import summarize_coverage
from scripts.import_point_in_time_bundle import import_bundle_document


def _bundle() -> dict:
    return {
        "as_of": "2026-03-02T21:00:00Z",
        "code_revision": "abc123",
        "datasets": {
            "graph": {"recording_version": 1, "queries": {}},
            "fundamentals": {"AAPL": {"market_cap": 1_000_000}},
            "universe": {"rows": [{"symbol": "AAPL"}]},
            "news": {
                "alpaca": [],
                "google": [],
                "benzinga": {},
            },
        },
    }


class _Registry:
    def __init__(self):
        self.calls = []

    def finalize_bundle(self, **kwargs):
        self.calls.append(kwargs)
        from point_in_time_registry import InMemoryPointInTimeRegistry

        return InMemoryPointInTimeRegistry().finalize_bundle(**kwargs)


def test_import_defaults_to_validating_dry_run_without_mutation():
    registry = _Registry()

    result = import_bundle_document(_bundle(), registry=registry)

    assert result["applied"] is False
    assert result["manifest_id"].startswith("pit-")
    assert registry.calls == []


def test_import_requires_explicit_apply_for_registry_mutation():
    registry = _Registry()

    result = import_bundle_document(
        _bundle(),
        apply=True,
        registry=registry,
    )

    assert result["applied"] is True
    assert len(registry.calls) == 1


def test_import_rejects_declared_hash_mismatch():
    payload = _bundle()
    payload["source_hashes"] = {
        name: "sha256:" + "0" * 64
        for name in ("graph", "fundamentals", "universe", "news")
    }

    with pytest.raises(PointInTimeDataError, match="declared source hashes"):
        import_bundle_document(payload)


def test_import_rejects_secret_shaped_payload_keys():
    payload = _bundle()
    payload["datasets"]["fundamentals"]["AAPL"]["api_key"] = "forbidden"

    with pytest.raises(PointInTimeDataError, match="secret-bearing"):
        import_bundle_document(payload)


def test_import_rejects_ignored_top_level_fields():
    payload = _bundle()
    payload["api_key"] = "must-not-be-ignored"

    with pytest.raises(PointInTimeDataError, match="unsupported top-level"):
        import_bundle_document(payload)


def test_coverage_reports_months_gaps_and_incomplete_manifests():
    manifests = [
        {
            "as_of": "2026-01-05T21:00:00Z",
            "status": "finalized",
            "provenance": "strict_verified",
            "source_hashes": {
                name: f"sha256:{name}"
                for name in ("graph", "fundamentals", "universe", "news")
            },
        },
        {
            "as_of": "2026-02-02T21:00:00Z",
            "status": "finalized",
            "provenance": "strict_verified",
            "source_hashes": {
                name: f"sha256:{name}"
                for name in ("graph", "fundamentals", "universe")
            },
        },
    ]

    report = summarize_coverage(
        manifests,
        legacy_row_count=7,
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 6),
        expected_sessions=(date(2026, 1, 5), date(2026, 1, 6)),
    )

    assert report["verified_month_count"] == 1
    assert report["legacy_row_count"] == 7
    assert report["finalized_dates"] == ["2026-01-05"]
    assert report["missing_sessions"] == ["2026-01-06"]
    assert report["incomplete_manifests"] == [
        {
            "as_of": "2026-02-02T21:00:00Z",
            "missing_datasets": ["news"],
        }
    ]
    assert "payload" not in str(report).lower()
