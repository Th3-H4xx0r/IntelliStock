"""A fingerprint must survive a jsonb round trip unchanged.

Spec section 2.4: canonical_sha256 is the single hashing entry point, so that
"a fingerprint is invariant to jsonb's key reordering and number
renormalisation (1.230e-5 -> 0.00001230)". paired_state_attest compares the
26 _ALLOWED_STATE_TABLES' fingerprint across the cutover, so a fingerprint
that changes just by being stored would either flag a false mismatch or mask
a real one.
"""
import pytest

from db import json as dbjson
from db import pool as dbpool
from db import schema, store

from .conftest import requires_pg

# Values whose Python repr and Postgres numeric rendering disagree.
_TRICKY = [
    1.0000000272564224e+16,
    1e22,
    1e15,
    1.23e-5,
    2.5e-10,
    5.0,
    0.1,
    123456789.123456789,
    -1e18,
    0.0,
]


def test_normalize_numbers_collapses_integral_floats():
    assert dbjson.normalize_numbers(1e16) == 10000000000000000
    assert isinstance(dbjson.normalize_numbers(1e16), int)
    assert dbjson.normalize_numbers(0.1) == 0.1


def test_normalize_numbers_leaves_booleans_alone():
    """bool is an int subclass; collapsing it would turn True into 1."""
    got = dbjson.normalize_numbers({"a": True, "b": False})
    assert got == {"a": True, "b": False}
    assert isinstance(got["a"], bool) and isinstance(got["b"], bool)


def test_normalize_numbers_recurses():
    assert dbjson.normalize_numbers({"a": [1e16, {"b": 2.0}]}) == \
        {"a": [10000000000000000, {"b": 2}]}


def test_canonical_is_key_order_invariant():
    assert dbjson.canonical_sha256({"a": 1, "b": {"c": 2, "d": 3}}) == \
        dbjson.canonical_sha256({"b": {"d": 3, "c": 2}, "a": 1})


@pytest.mark.parametrize("value", _TRICKY)
def test_canonical_is_stable_under_integral_float_collapse(value):
    """Whatever the storage layer does to an integral float, the fingerprint
    must not move."""
    collapsed = int(value) if float(value).is_integer() else value
    assert dbjson.canonical_sha256({"v": value}) == \
        dbjson.canonical_sha256({"v": collapsed})


@requires_pg
@pytest.mark.parametrize("value", _TRICKY)
def test_fingerprint_survives_a_real_jsonb_round_trip(pg_schema, value):
    schema.ensure_schema(tables=["DiscordOutbox"])
    doc = {"id": "fp", "v": value, "nested": {"z": 1, "a": [value, 2.0]}}
    before = dbjson.canonical_sha256(doc)
    store.insert("DiscordOutbox", doc, conflict="replace")
    after = dbjson.canonical_sha256(store.get("DiscordOutbox", "fp"))
    assert before == after, "fingerprint moved for %r" % value


@requires_pg
def test_the_documented_renormalisation_example_holds(pg_schema):
    """The spec's own example: 1.230e-5 -> 0.00001230."""
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT (%s::jsonb) #>> '{}' AS t", ("1.230e-5",))
        assert cur.fetchone()["t"] == "0.00001230"
    doc = {"id": "x", "v": 1.230e-5}
    store.insert("DiscordOutbox", doc, conflict="replace")
    assert dbjson.canonical_sha256(store.get("DiscordOutbox", "x")) == \
        dbjson.canonical_sha256(doc)


@requires_pg
def test_a_realistic_state_row_fingerprints_identically_after_storage(pg_schema):
    """The shape paired_state_attest actually hashes."""
    schema.ensure_schema(tables=["NexusRuntimeState"])
    doc = {
        "id": "alpaca-main:cfg",
        "pnl": 1e16, "pnl_percent": 4.2, "bars": 3.0,
        "weights": {"AAPL": 0.25, "MSFT": 1e17, "NVDA": 0.0},
        "history": [1e16, 2.5, 3.0],
    }
    before = dbjson.canonical_sha256(doc)
    store.insert("NexusRuntimeState", doc, conflict="replace")
    assert dbjson.canonical_sha256(store.get("NexusRuntimeState", "alpaca-main:cfg")) \
        == before
