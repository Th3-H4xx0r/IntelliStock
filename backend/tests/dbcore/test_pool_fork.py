import os
import pytest

from db import pool as dbpool
from db.errors import UnavailableError

from .conftest import requires_pg


def test_dsn_from_env_prefers_pg_dsn(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://u@h:5555/dbx")
    assert dbpool.dsn_from_env() == "postgresql://u@h:5555/dbx"


def test_dsn_from_env_assembles_from_parts_with_defaults(monkeypatch):
    monkeypatch.delenv("PG_DSN", raising=False)
    for k in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
              "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    dsn = dbpool.dsn_from_env()
    assert "host=localhost" in dsn and "port=5432" in dsn
    assert "user=intellistock" in dsn and "dbname=IntelliStock" in dsn


def test_dsn_from_env_includes_password_when_set(monkeypatch):
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    assert "password=s3cret" in dbpool.dsn_from_env()


def test_unreachable_host_raises_unavailable_not_a_driver_error(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://postgres@127.0.0.1:1/nope")
    monkeypatch.setenv("PG_CONNECT_RETRIES", "0")
    monkeypatch.setenv("PG_POOL_TIMEOUT", "2")
    monkeypatch.setenv("PG_RECONNECT_TIMEOUT", "2")
    dbpool.close_pool()
    with pytest.raises(UnavailableError):
        with dbpool.connection():
            pass
    dbpool.close_pool()


@requires_pg
def test_get_pool_is_idempotent_per_process(pg_schema):
    assert dbpool.get_pool() is dbpool.get_pool()


@requires_pg
def test_cursor_returns_dict_rows(pg_schema):
    with dbpool.cursor() as cur:
        cur.execute("SELECT 1 AS a, 'x' AS b")
        assert cur.fetchone() == {"a": 1, "b": "x"}


@requires_pg
def test_connections_run_in_utc(pg_schema):
    with dbpool.cursor() as cur:
        cur.execute("SHOW timezone")
        assert cur.fetchone()["TimeZone"] == "UTC"


@requires_pg
def test_transaction_isolation_option_survives_the_options_string(pg_schema):
    """libpq splits options on whitespace: an unescaped "read committed"
    arrives as "read" and the connection is rejected at startup."""
    with dbpool.cursor() as cur:
        cur.execute("SHOW default_transaction_isolation")
        assert cur.fetchone()["default_transaction_isolation"] == "read committed"


@requires_pg
def test_health_reports_ok_and_the_host(pg_schema):
    h = dbpool.health()
    assert h["ok"] is True and isinstance(h["size"], int) and h["dsn_host"]


@requires_pg
def test_listen_connection_is_autocommit_and_unpooled(pg_schema):
    conn = dbpool.listen_connection()
    try:
        assert conn.autocommit is True
        conn.execute("LISTEN some_channel")
    finally:
        conn.close()


@requires_pg
def test_forked_child_does_not_inherit_a_usable_pool(pg_schema):
    parent_pool = dbpool.get_pool()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            child_pool = dbpool.get_pool()
            with dbpool.cursor() as cur:
                cur.execute("SELECT 42 AS n")
                ok = cur.fetchone()["n"] == 42
            code = 0 if (ok and child_pool is not parent_pool) else 2
        except Exception:
            code = 3
        finally:
            os.write(write_fd, b"x")
            os._exit(code)
    os.close(write_fd)
    os.read(read_fd, 1)
    _, status = os.waitpid(pid, 0)
    os.close(read_fd)
    assert os.WEXITSTATUS(status) == 0
    with dbpool.cursor() as cur:
        cur.execute("SELECT 7 AS n")
        assert cur.fetchone()["n"] == 7
