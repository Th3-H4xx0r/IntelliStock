"""Exception hierarchy for the Postgres store.

Every failure the store surfaces is a ``StoreError`` or a subclass, so call
sites that used to catch RethinkDB driver exceptions have one class to catch.
"""
from __future__ import annotations


class StoreError(Exception):
    """Any store-level failure: bad input, a rejected write, a query error."""


class ConflictError(StoreError):
    """A primary-key conflict that ``conflict='error'`` did not absorb."""


class UnavailableError(StoreError):
    """The database could not be reached after the connection retry budget."""


class CasFailed(StoreError):
    """A compare-and-swap predicate did not hold. Raised only when the caller
    asks for it; ``store.replace_if`` returns ``None`` instead by default."""
