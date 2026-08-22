"""Canonical JSON encoding for the store.

Two jobs:

1. ``dumps``/``loads`` are what psycopg uses for every jsonb parameter and
   result. ``allow_nan=False`` makes NaN/Infinity raise ``ValueError`` at the
   client, mirroring RethinkDB's client-side rejection, instead of surfacing
   as a server-side "invalid input syntax for type json" three layers away.
2. ``canonical``/``canonical_sha256`` are the single hashing entry point.
   jsonb reorders keys and renormalises numbers (``1.230e-5`` becomes
   ``0.00001230``), so a fingerprint taken over raw bytes is not stable across
   a round trip. Canonicalising first makes it stable.
"""
from __future__ import annotations

import hashlib
import json as _json
from typing import Any, Union


def _has_nul(value: Any) -> bool:
    """Exact NUL scan. Only ever called when the cheap check already hit."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_nul(v) for v in value)
    return False


def dumps(value: Any) -> str:
    """Compact JSON. Raises ValueError on NaN/Infinity and on NUL.

    Postgres ``jsonb`` cannot represent U+0000 anywhere in a string -- the
    server answers with "unsupported Unicode escape sequence". Rejecting it
    here, like NaN, keeps the failure at the client with a message naming the
    cause instead of a driver error three layers away.
    """
    out = _json.dumps(value, allow_nan=False, separators=(",", ":"))
    # The cheap check can false-positive on a literal backslash-u-0000 in the
    # data, so confirm with an exact scan before raising.
    if "\\u0000" in out and _has_nul(value):
        raise ValueError(
            "NUL (U+0000) cannot be stored in a Postgres jsonb document")
    return out


def loads(value: Union[str, bytes]) -> Any:
    return _json.loads(value)


def canonical(value: Any) -> str:
    """Key-sorted JSON, used for hashing and for byte-comparison in tests."""
    return _json.dumps(value, sort_keys=True, allow_nan=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


_INSTALLED = False


def install() -> None:
    """Point psycopg's jsonb adapters at our dumps/loads. Idempotent.

    Called once from ``pool.get_pool()`` so nothing has to remember to call it.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    from psycopg.types.json import set_json_dumps, set_json_loads
    set_json_dumps(dumps)
    set_json_loads(loads)
    _INSTALLED = True
