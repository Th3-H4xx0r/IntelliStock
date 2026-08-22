"""pytest conftest for backend/tests.

Production runs broker.py with ``backend/`` on sys.path, so internal modules
use bare imports (e.g., ``from strategy_cache_persistence import ...``).
When pytest runs from the repo root, ``backend/`` is NOT on sys.path by
default, which breaks any lazy bare-import a backend module performs at call
time. We mirror the production layout here.

Idempotent: each individual test file may also add backend/ to sys.path; the
``if`` guard makes this a no-op in that case.
"""
import contextlib
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


import pytest

if not os.environ.get("PG_TEST_DSN"):
    # No database in this run, but a unit test can still reach db.store through
    # a module it exercises (the ported call sites no longer take a connection
    # object that fails fast on a dummy). The pool would then spend
    # PG_POOL_TIMEOUT + PG_RECONNECT_TIMEOUT -- 60s by default -- discovering
    # there is nothing to connect to. Fail in a second instead.
    os.environ.setdefault("PG_POOL_TIMEOUT", "1")
    os.environ.setdefault("PG_RECONNECT_TIMEOUT", "1")
    os.environ.setdefault("PG_CONNECT_RETRIES", "0")


# ---------------------------------------------------------------------------
# Real-Postgres fixtures, shared by every test under backend/tests.
#
# They used to live in backend/tests/dbcore/conftest.py, which made them
# invisible to flat backend/tests/*.py files. dbcore/conftest.py re-exports
# them, so its `from .conftest import requires_pg` importers keep working.
# ---------------------------------------------------------------------------

PG_TEST_DSN = os.environ.get("PG_TEST_DSN")
requires_pg = pytest.mark.skipif(
    not PG_TEST_DSN, reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


@contextlib.contextmanager
def _throwaway_schema():
    """Create a Postgres schema, point the pool's search_path at it, drop it.

    The single owner of the pool/search-path/teardown dance: two copies of it
    in the file every backend test loads would drift.

    Inert until a fixture enters it -- importing this module must not touch a
    database, which is why the db imports live inside the body.
    """
    import uuid

    from db import pool as dbpool
    name = "t_" + uuid.uuid4().hex[:16]
    dbpool.close_pool()
    prior_dsn = os.environ.get("PG_DSN")
    os.environ["PG_DSN"] = PG_TEST_DSN
    os.environ.pop("PG_SEARCH_PATH", None)
    with dbpool.connection(autocommit=True) as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS "%s"' % name)
    dbpool.close_pool()
    os.environ["PG_SEARCH_PATH"] = name
    try:
        yield name
    finally:
        os.environ.pop("PG_SEARCH_PATH", None)
        dbpool.close_pool()
        with dbpool.connection(autocommit=True) as conn:
            conn.execute('DROP SCHEMA IF EXISTS "%s" CASCADE' % name)
        dbpool.close_pool()
        # Restore, never leak: the test DSN outliving the fixture pointed the
        # rest of the session's pool at the scratch cluster.
        if prior_dsn is None:
            os.environ.pop("PG_DSN", None)
        else:
            os.environ["PG_DSN"] = prior_dsn


@pytest.fixture
def pg_schema():
    """A throwaway schema with no tables in it. Yields the schema name."""
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    with _throwaway_schema() as name:
        yield name


@pytest.fixture
def store(request):
    """The store under test: real Postgres when PG_TEST_DSN is set, else a
    FakeStore over Python dicts.

    Replaces the ad-hoc ``monkeypatch.setattr(iu, "r", fake_r)`` pattern in
    the ~30 test files that stub RethinkDB today. Each test gets fresh state:
    real PG gets a per-test schema (pg_schema, requested lazily so the fake
    path is never skipped for want of a DSN), fake gets a fresh instance.
    """
    if not PG_TEST_DSN:
        from db.fake import FakeStore
        yield FakeStore()
        return
    request.getfixturevalue("pg_schema")
    from db import schema as dbschema
    from db import store as real_store
    dbschema.ensure_schema()
    yield real_store
