"""The 9 kalshi tables whose primary key is not ``id``.

RethinkDB minted a uuid only for a table keyed on ``id``; a document missing
a table's custom primary key raised there ("Primary key `fixture_key` not
found in document"). The store auto-minted for every table, so a kalshi
document that lost its key was written under a uuid nothing ever reads --
silent data loss instead of a loud rejection. These are the regressions.
"""
import pytest

from db import schema as dbschema
from db.errors import StoreError

# (table, primary key) for every kalshi table the registry keys on a field
# other than ``id`` -- the list backend/kalshi/db.py's KALSHI_TABLES declares.
CUSTOM_PK_TABLES = [
    ("sports_fixtures", "fixture_id"),
    ("lineups", "fixture_id"),
    ("match_features", "fixture_id"),
    ("kalshi_market_listings", "fixture_id"),
    ("kalshi_markets", "market_ticker"),
    ("kalshi_orders", "client_order_id"),
    ("kalshi_scan_budget", "window"),
    ("kalshi_capital_plan", "instance_id"),
    ("KalshiHistFixtures", "fixture_key"),
]


def test_registry_agrees_with_kalshi_db_on_every_custom_primary_key():
    for table, pk in CUSTOM_PK_TABLES:
        assert dbschema.spec(table).pk_field == pk, table


@pytest.mark.parametrize("table,pk", CUSTOM_PK_TABLES,
                         ids=[t for t, _ in CUSTOM_PK_TABLES])
def test_missing_pk_field_raises(store, table, pk):
    """No uuid, no row: a custom-pk document without its key is rejected."""
    with pytest.raises(StoreError) as exc:
        store.insert(table, {"payload": 1})
    assert pk in str(exc.value)
    assert store.count(table) == 0


@pytest.mark.parametrize("table,pk", CUSTOM_PK_TABLES,
                         ids=[t for t, _ in CUSTOM_PK_TABLES])
def test_pk_present_inserts_under_that_key(store, table, pk):
    res = store.insert(table, {pk: "k1", "payload": 1})
    assert (res["inserted"], res["errors"]) == (1, 0)
    assert res["generated_keys"] == []
    assert store.get(table, "k1")["payload"] == 1


def test_a_null_pk_value_is_rejected_too(store):
    with pytest.raises(StoreError):
        store.insert("kalshi_markets", {"market_ticker": None, "payload": 1})
    assert store.count("kalshi_markets") == 0
