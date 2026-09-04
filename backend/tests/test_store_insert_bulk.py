"""db.store.insert_bulk: the one-round-trip upsert for large idempotent loads."""
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from outlier_features import FEATURES_TABLE  # noqa: E402


def _docs():
    return [{"id": f"2026-06-0{i}|AAA", "date": f"2026-06-0{i}", "symbol": "AAA",
             "close": float(i)} for i in (1, 2, 3)]


def test_insert_bulk_upserts_and_is_idempotent(store):
    docs = _docs()
    r = store.insert_bulk(FEATURES_TABLE, docs, conflict="replace")
    assert r.errors == 0
    assert store.get(FEATURES_TABLE, "2026-06-02|AAA")["close"] == 2.0
    docs[1]["close"] = 22.0
    store.insert_bulk(FEATURES_TABLE, docs, conflict="replace")
    assert store.get(FEATURES_TABLE, "2026-06-02|AAA")["close"] == 22.0
    rows = store.run(store.between(FEATURES_TABLE, "2026-06-01|", "2026-06-04|"))
    assert len(rows) == 3


def test_insert_bulk_rejects_unknown_conflict_modes(store):
    from db.errors import StoreError
    with pytest.raises(StoreError):
        store.insert_bulk(FEATURES_TABLE, _docs(), conflict="merge")


def test_insert_bulk_refuses_partitioned_tables():
    if os.environ.get("PG_TEST_DSN"):
        pytest.skip("real-store DDL semantics are covered by the PG suites")
    from db import store as real
    from db.errors import StoreError
    with pytest.raises(StoreError):
        real.insert_bulk("PriceHistory", [{"id": "x", "ticker": "AAA", "ts": "2026-01-01T00:00:00Z"}])


def test_insert_bulk_last_write_wins_for_an_id_repeated_inside_one_batch(store):
    """scripts/build_outlier_features.py feeds 5000-row slices with no dedup;
    a repeated key must not fail the chunk, and the later row must land."""
    docs = _docs() + [{"id": "2026-06-02|AAA", "date": "2026-06-02", "symbol": "AAA", "close": 99.0}]
    r = store.insert_bulk(FEATURES_TABLE, docs, conflict="replace")
    assert r.errors == 0
    assert store.get(FEATURES_TABLE, "2026-06-02|AAA")["close"] == 99.0
    assert r.inserted + r.replaced + r.unchanged == 3


def test_insert_bulk_reports_replaced_and_unchanged_like_insert(store):
    first = store.insert_bulk(FEATURES_TABLE, _docs(), conflict="replace")
    assert (first.inserted, first.replaced, first.errors) == (3, 0, 0)
    again = store.insert_bulk(FEATURES_TABLE, _docs(), conflict="replace")
    assert again.inserted == 0 and again.errors == 0
    assert again.replaced + again.unchanged == 3


def test_insert_bulk_counts_duplicates_as_errors_under_conflict_error(store):
    store.insert_bulk(FEATURES_TABLE, _docs()[:1], conflict="error")
    r = store.insert_bulk(FEATURES_TABLE, _docs(), conflict="error")
    assert r.errors == 1 and r.first_error and "Duplicate" in r.first_error
    assert r.inserted == 2
