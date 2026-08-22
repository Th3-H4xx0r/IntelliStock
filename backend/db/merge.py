"""ReQL-compatible deep merge.

``.update({...})`` in RethinkDB merges nested objects recursively and replaces
arrays and scalars wholesale. 149 call sites depend on that, and the codebase
has already been burned once by the semantics (purge_backtest_secrets.py:101).
``r.literal(v)`` opts out: it sets the subtree shallow instead of merging into
it.

This module is pure. Its SQL twin ``jsonb_deep_merge`` lives in schema.py and
is proved equivalent by the Hypothesis property test in
backend/tests/dbcore/test_merge_property.py.
"""
from __future__ import annotations

from typing import Any

from .errors import StoreError

LITERAL_KEY = "__db_literal__"


class Literal:
    """ReQL ``r.literal()``: replace the subtree, do not merge into it."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Literal) and other.value == self.value

    def __hash__(self) -> int:
        return hash(("Literal", repr(self.value)))

    def __repr__(self) -> str:
        return "Literal(%r)" % (self.value,)


def _is_sentinel(value: Any) -> bool:
    return isinstance(value, dict) and len(value) == 1 and LITERAL_KEY in value


def deep_merge(base: Any, patch: Any) -> Any:
    """Merge ``patch`` into ``base`` with ReQL ``update`` semantics.

    Objects merge recursively. Arrays and scalars replace. ``None`` sets JSON
    null (it does NOT delete the key). Missing intermediate objects are
    created. ``Literal(v)`` and the wire sentinel ``{"__db_literal__": v}``
    both set ``v`` shallow.
    """
    if isinstance(patch, Literal):
        return _strip(patch.value)
    if _is_sentinel(patch):
        return _strip(patch[LITERAL_KEY])
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return _strip(patch)
    out = dict(base)
    for key, value in patch.items():
        if key in out:
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = deep_merge({}, value) if _mergeable(value) else _strip(value)
    return out


def _mergeable(value: Any) -> bool:
    return isinstance(value, dict) and not _is_sentinel(value)


def _strip(value: Any) -> Any:
    """Unwrap Literal/sentinel markers anywhere inside a replacement value."""
    if isinstance(value, Literal):
        return _strip(value.value)
    if _is_sentinel(value):
        return _strip(value[LITERAL_KEY])
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def encode_patch(patch: Any) -> Any:
    """Rewrite ``Literal(v)`` into ``{"__db_literal__": v}`` so a patch can
    travel to Postgres as one jsonb parameter."""
    if isinstance(patch, Literal):
        return {LITERAL_KEY: encode_patch(patch.value)}
    if isinstance(patch, dict):
        if LITERAL_KEY in patch:
            raise StoreError(
                "document key %r collides with the r.literal wire sentinel" % LITERAL_KEY
            )
        return {k: encode_patch(v) for k, v in patch.items()}
    if isinstance(patch, list):
        return [encode_patch(v) for v in patch]
    return patch
