"""Postgres store package. Nothing outside this package opens a connection.

Import surface for the whole repo::

    from db import store, watch, Literal, P
    from db.errors import StoreError, ConflictError, UnavailableError, CasFailed
"""
from __future__ import annotations

from . import json, merge, pool, schema, store, watch
from .errors import CasFailed, ConflictError, StoreError, UnavailableError
from .merge import Literal
from .store import P

__all__ = [
    "store", "watch", "schema", "pool", "merge", "json",
    "Literal", "P",
    "StoreError", "ConflictError", "UnavailableError", "CasFailed",
]
