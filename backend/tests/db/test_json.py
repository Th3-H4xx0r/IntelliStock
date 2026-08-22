import math
import pytest

from db import json as dbjson


def test_dumps_is_compact_and_stable():
    assert dbjson.dumps({"b": 1, "a": 2}) == '{"b":1,"a":2}'


def test_dumps_rejects_nan():
    with pytest.raises(ValueError):
        dbjson.dumps({"x": float("nan")})


def test_dumps_rejects_infinity():
    with pytest.raises(ValueError):
        dbjson.dumps({"x": math.inf})


def test_loads_roundtrips():
    assert dbjson.loads('{"a":[1,2,{"b":null}]}') == {"a": [1, 2, {"b": None}]}


def test_canonical_sorts_keys_at_every_depth():
    assert dbjson.canonical({"b": {"z": 1, "a": 2}, "a": 3}) == \
        '{"a": 3, "b": {"a": 2, "z": 1}}'


def test_canonical_is_key_order_invariant():
    left = {"a": 1, "b": {"c": 2, "d": 3}}
    right = {"b": {"d": 3, "c": 2}, "a": 1}
    assert dbjson.canonical_sha256(left) == dbjson.canonical_sha256(right)


def test_canonical_sha256_is_a_hex_digest():
    digest = dbjson.canonical_sha256({"a": 1})
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_canonical_rejects_nan():
    with pytest.raises(ValueError):
        dbjson.canonical({"x": float("nan")})
