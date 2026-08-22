"""Real-Postgres fixtures for backend/tests/dbcore.

Every test here needs a database. Without PG_TEST_DSN they skip: only a real
Postgres can prove collation, jsonb_deep_merge, and LISTEN/NOTIFY.
Each test gets its own Postgres *schema* so tests never share state.
"""
import os
import uuid

import pytest

PG_TEST_DSN = os.environ.get("PG_TEST_DSN")
requires_pg = pytest.mark.skipif(
    not PG_TEST_DSN, reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


@pytest.fixture
def pg_schema():
    """Create a throwaway schema, point the pool's search_path at it, drop it."""
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    from db import pool as dbpool
    name = "t_" + uuid.uuid4().hex[:16]
    dbpool.close_pool()
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
