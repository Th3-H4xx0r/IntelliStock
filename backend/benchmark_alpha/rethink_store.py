"""Compatibility alias for ``benchmark_alpha.pg_store``.

The module was named for the driver, not for what it does. The store moved to
Postgres in the 2026-08-22 port; this module keeps the old import path (and
the old class names) working for the call sites owned by other port groups --
``broker.py``, ``api/main.py``, ``backend/scripts/run_alpha_research.py`` --
and for the ~10 test files that import from here. Nothing new should import
this name: use ``benchmark_alpha.pg_store``.
"""
from benchmark_alpha.pg_store import *          # noqa: F401,F403
from benchmark_alpha.pg_store import (          # noqa: F401
    DB_NAME,
    EVENTS_TABLE,
    EXPERIMENTS_TABLE,
    EXPERIMENT_OUTCOMES_TABLE,
    STATE_TABLE,
    AlphaIntegrityError,
    AlphaPostgresStore,
    AlphaRethinkStore,
    AlphaStateConflictError,
    AlphaStoreHealth,
    AlphaUnavailableError,
    PostgresBackend,
    RunStateRecord,
    StateRecord,
    _RethinkBackend,
    advance_page,
    canonical_json,
    ensure_alpha_tables,
    run_state_key,
)
