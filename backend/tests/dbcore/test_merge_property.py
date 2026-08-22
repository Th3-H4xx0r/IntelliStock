"""jsonb_deep_merge in Postgres must equal deep_merge in Python, byte for byte.

10,000 Hypothesis-generated (base, patch) pairs including Literal markers.
This is mandatory and blocks merge.py sign-off (spec section 2.5).
"""
import time

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from db import json as dbjson
from db import pool as dbpool
from db import schema
from db.merge import Literal, deep_merge, encode_patch

from .conftest import requires_pg

_scalars = st.one_of(
    st.none(), st.booleans(),
    st.integers(min_value=-10**9, max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=12),
)
_json = st.recursive(
    _scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=6), children, max_size=4),
    ),
    max_leaves=12,
)
_objects = st.dictionaries(st.text(min_size=1, max_size=6), _json, max_size=5)
_patches = st.recursive(
    _json,
    lambda children: st.one_of(
        children.map(Literal),
        st.dictionaries(st.text(min_size=1, max_size=6), children, max_size=4),
    ),
    max_leaves=10,
).filter(lambda v: isinstance(v, dict))


def _contains_nul(value):
    """Postgres jsonb cannot represent U+0000 anywhere. db.json.dumps rejects
    it at the client, so those inputs have no SQL merge to compare against --
    the property below asserts the rejection instead of skipping them."""
    if isinstance(value, Literal):
        return _contains_nul(value.value)
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_contains_nul(k) or _contains_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_nul(v) for v in value)
    return False


def _sql_merge(base, patch):
    with dbpool.cursor() as cur:
        cur.execute("SELECT jsonb_deep_merge(%s::jsonb, %s::jsonb) AS m",
                    (dbjson.dumps(base), dbjson.dumps(encode_patch(patch))))
        return cur.fetchone()["m"]


def _sql_roundtrip(value):
    """A bare trip through jsonb, no merge involved.

    jsonb stores numbers as ``numeric``, so 1.0000000272564224e+16 comes back
    as 10000000272564224 whether or not it was merged. That is a storage
    property, not a merge property, so the Python side is round-tripped too
    and the comparison isolates merge SEMANTICS. See
    test_jsonb_renormalises_numbers_on_any_round_trip for the effect itself.
    """
    with dbpool.cursor() as cur:
        cur.execute("SELECT %s::jsonb AS v", (dbjson.dumps(value),))
        return cur.fetchone()["v"]


@requires_pg
@settings(max_examples=10000, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(base=_objects, patch=_patches)
def test_sql_and_python_deep_merge_agree(pg_schema, base, patch):
    schema.ensure_schema(tables=[])          # installs jsonb_deep_merge only
    if _contains_nul(base) or _contains_nul(patch):
        with pytest.raises(ValueError):
            dbjson.dumps({"base": base, "patch": encode_patch(patch)})
        return
    assert dbjson.canonical(_sql_merge(base, patch)) == \
        dbjson.canonical(_sql_roundtrip(deep_merge(base, patch)))


@requires_pg
def test_merging_two_empty_objects_is_an_empty_object_not_null(pg_schema):
    """jsonb_object_agg over zero rows is SQL NULL, so an unguarded
    jsonb_deep_merge('{}','{}') returned null and, nested, rewrote
    {"a": {}} to {"a": null}: silent document corruption."""
    schema.ensure_schema(tables=[])
    assert _sql_merge({}, {}) == {}
    assert _sql_merge({"a": {}}, {"a": {}}) == {"a": {}}
    assert _sql_merge({"a": {"b": 1}}, {"a": {}}) == {"a": {"b": 1}}


@requires_pg
def test_a_nul_character_is_rejected_at_the_client(pg_schema):
    with pytest.raises(ValueError):
        dbjson.dumps({"k": "a\x00b"})
    with pytest.raises(ValueError):
        dbjson.dumps({"a\x00b": 1})
    # A literal backslash-u-0000 in the data is NOT a NUL and must survive.
    assert dbjson.dumps({"k": "a\\u0000b"}) == '{"k":"a\\\\u0000b"}'


@requires_pg
def test_literal_blanks_a_subtree_in_sql_too(pg_schema):
    schema.ensure_schema(tables=[])
    got = _sql_merge({"s": {"k": "secret"}}, {"s": Literal({})})
    assert got == {"s": {}}


@requires_pg
def test_deep_merge_on_a_realistic_config_stays_under_10ms(pg_schema):
    """Risk #5: doc 179 is 758 deep keys / 28 KB, doc 180 is 963 keys.

    If a single deep merge exceeds 10 ms we materialise the merge in Python
    and write the whole doc for the handful of large-config tables.
    Correctness is unaffected either way -- this test exists to make the
    decision on evidence rather than on a hunch.
    """
    schema.ensure_schema(tables=[])
    deep = {"lvl%d" % i: {"a": i, "b": [1, 2, 3], "c": {"d": "x" * 24}}
            for i in range(200)}
    start = time.perf_counter()
    for _ in range(10):
        _sql_merge(deep, {"lvl7": {"a": 99}})
    per_call_ms = (time.perf_counter() - start) / 10 * 1000
    assert per_call_ms < 10.0, "deep merge took %.2f ms/call" % per_call_ms


@requires_pg
def test_jsonb_renormalises_numbers_on_any_round_trip(pg_schema):
    """The documented storage effect the property test isolates: jsonb keeps
    numbers as ``numeric``, so the textual form changes with no merge in
    sight. Fingerprints must be taken over db.json.canonical of the value
    READ BACK, never over the bytes that were written."""
    schema.ensure_schema(tables=[])
    assert _sql_roundtrip({"a": 1.0000000272564224e+16}) == {"a": 10000000272564224}
    assert _sql_roundtrip({"a": 1.230e-5}) == {"a": 1.23e-05}
    # Merging changes nothing about it: the same value survives a merge
    # exactly as it survives a bare round trip.
    assert _sql_merge({"a": 1.0000000272564224e+16}, {}) == \
        _sql_roundtrip({"a": 1.0000000272564224e+16})


@requires_pg
def test_nested_literal_sentinels_are_stripped_from_a_replacement(pg_schema):
    """A wire sentinel must never survive into the stored document.

    The SQL merge used to return `b -> '__db_literal__'` verbatim, so
    Literal(Literal(None)) stored {"__db_literal__": null} where Python's
    deep_merge stored null -- and any Literal nested inside a replacement
    subtree or array leaked the same way.
    """
    schema.ensure_schema(tables=[])
    for base, patch in (
        ({}, {"k": Literal(Literal(None))}),
        ({"k": {"old": 1}}, {"k": Literal({"a": Literal(1)})}),
        ({}, {"k": Literal([Literal(1), {"b": Literal("x")}])}),
        ({"k": 1}, {"k": Literal(Literal(Literal(2)))}),
    ):
        got = _sql_merge(base, patch)
        assert got == _sql_roundtrip(deep_merge(base, patch))
        assert "__db_literal__" not in dbjson.canonical(got), got
