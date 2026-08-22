"""pytest conftest for backend/tests.

Production runs broker.py with ``backend/`` on sys.path, so internal modules
use bare imports (e.g., ``from strategy_cache_persistence import ...``).
When pytest runs from the repo root, ``backend/`` is NOT on sys.path by
default, which breaks any lazy bare-import a backend module performs at call
time. We mirror the production layout here.

Idempotent: each individual test file may also add backend/ to sys.path; the
``if`` guard makes this a no-op in that case.
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


import pytest


@pytest.fixture
def store():
    """The store under test: real Postgres when PG_TEST_DSN is set, else a
    FakeStore over Python dicts.

    Replaces the ad-hoc ``monkeypatch.setattr(iu, "r", fake_r)`` pattern in
    the ~30 test files that stub RethinkDB today. Each test gets fresh state:
    real PG gets a per-test schema dropped on teardown, fake gets a fresh
    instance.

    Inert until a test asks for it -- importing this module must not touch a
    database, which is why every db import lives inside the fixture body.
    """
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        from db.fake import FakeStore
        yield FakeStore()
        return
    import uuid
    from db import pool as dbpool
    from db import schema as dbschema
    from db import store as real_store
    name = "t_" + uuid.uuid4().hex[:16]
    dbpool.close_pool()
    prior_dsn = os.environ.get("PG_DSN")
    os.environ["PG_DSN"] = dsn
    os.environ.pop("PG_SEARCH_PATH", None)
    with dbpool.connection(autocommit=True) as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS "%s"' % name)
    dbpool.close_pool()
    os.environ["PG_SEARCH_PATH"] = name
    try:
        dbschema.ensure_schema()
        yield real_store
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
