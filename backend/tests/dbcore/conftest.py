"""Real-Postgres fixtures for backend/tests/dbcore.

Every test here needs a database. Without PG_TEST_DSN they skip: only a real
Postgres can prove collation, jsonb_deep_merge, and LISTEN/NOTIFY.
Each test gets its own Postgres *schema* so tests never share state.

The fixtures themselves now live in backend/tests/conftest.py so that flat
backend/tests/*.py files can use them too; this module re-exports them
because a dozen dbcore tests do ``from .conftest import requires_pg``.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from conftest import PG_TEST_DSN, pg_schema, requires_pg  # noqa: F401,E402
