"""One psycopg_pool.ConnectionPool per process, created lazily after fork.

Postgres connections are not process-safe. Every process that forks (server ->
broker subprocess, backtest engine -> container, FastAPI workers) must not
inherit a live pool, so the pool is created on first use, never at import, and
``os.register_at_fork`` drops the child's inherited object without closing the
parent's sockets.

Cursors are not thread-safe; connections are. The pool hands out one
connection per operation via a context manager, so no cursor crosses a thread
boundary. Watchers get their own dedicated autocommit connection OUTSIDE the
pool -- a LISTEN session must never sit in a pooled connection or inside a
long transaction.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from typing import Any, Iterator, Optional

from . import json as dbjson
from .errors import UnavailableError

DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 8            # env PG_POOL_MAX

_pool: Optional[Any] = None      # psycopg_pool.ConnectionPool
_pool_pid: Optional[int] = None
_lock = threading.RLock()


def dsn_from_env() -> str:
    """PG_DSN wins; otherwise assemble from the POSTGRES_* parts."""
    dsn = os.environ.get("PG_DSN")
    if dsn:
        return dsn
    parts = [
        "host=%s" % os.environ.get("POSTGRES_HOST", "localhost"),
        "port=%s" % os.environ.get("POSTGRES_PORT", "5432"),
        "user=%s" % os.environ.get("POSTGRES_USER", "intellistock"),
        "dbname=%s" % os.environ.get("POSTGRES_DB", "IntelliStock"),
    ]
    password = os.environ.get("POSTGRES_PASSWORD")
    if password:
        parts.append("password=%s" % password)
    return " ".join(parts)


def _options() -> str:
    # libpq splits the options string on whitespace, so a value containing a
    # space must be backslash-escaped. "read committed" without the escape
    # arrives as "read" and the server rejects the connection at startup.
    opts = ["-c timezone=UTC",
            "-c default_transaction_isolation=read\\ committed"]
    search_path = os.environ.get("PG_SEARCH_PATH")
    if search_path:
        # Test isolation: each test runs in its own schema. Used VERBATIM --
        # appending ",public" would let CREATE TABLE IF NOT EXISTS resolve an
        # existing public copy and silently skip creating the test one, so
        # every test would quietly share public's state.
        opts.append("-c search_path=%s" % search_path)
    return " ".join(opts)


def get_pool(dsn: Optional[str] = None):
    """Idempotent per process. Rebuilds if the pid changed (post-fork)."""
    global _pool, _pool_pid
    with _lock:
        if _pool is not None and _pool_pid == os.getpid():
            return _pool
        if _pool is not None and _pool_pid != os.getpid():
            _pool = None          # inherited across a fork: abandon, never close
        dbjson.install()
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(
            conninfo=dsn or dsn_from_env(),
            min_size=int(os.environ.get("PG_POOL_MIN", DEFAULT_MIN_SIZE)),
            max_size=int(os.environ.get("PG_POOL_MAX", DEFAULT_MAX_SIZE)),
            kwargs={"options": _options(), "row_factory": dict_row},
            open=True,
            timeout=float(os.environ.get("PG_POOL_TIMEOUT", "30")),
            # Without this the pool retries a bad DSN forever and every caller
            # hangs instead of getting UnavailableError.
            reconnect_timeout=float(os.environ.get("PG_RECONNECT_TIMEOUT", "30")),
        )
        _pool_pid = os.getpid()
        return _pool


_RETRY_DELAYS = (0.5, 1.5)


@contextlib.contextmanager
def connection(*, autocommit: bool = False) -> Iterator[Any]:
    """Check a connection out of the pool.

    A connection-level failure is retried twice (0.5s, 1.5s) before raising
    UnavailableError -- the same shape as today's
    ``broker.py:1897 get_conn_retry(max_attempts, delay)``, whose call sites
    keep their own outer loops. Query-level errors are NEVER retried: a
    retried non-idempotent write is worse than an error.
    """
    import psycopg
    from psycopg_pool import PoolTimeout

    budget = int(os.environ.get("PG_CONNECT_RETRIES", str(len(_RETRY_DELAYS))))
    attempt = 0
    while True:
        try:
            pool = get_pool()
            with pool.connection() as conn:
                if autocommit and not conn.autocommit:
                    conn.autocommit = True
                yield conn
            return
        except (psycopg.OperationalError, PoolTimeout, OSError) as exc:
            if attempt >= budget:
                raise UnavailableError("postgres unavailable: %s" % exc) from exc
            time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
            attempt += 1
            close_pool()


@contextlib.contextmanager
def cursor(*, autocommit: bool = False) -> Iterator[Any]:
    """A dict-row cursor on a pooled connection."""
    from psycopg.rows import dict_row
    with connection(autocommit=autocommit) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur


def listen_connection():
    """A dedicated, unpooled, autocommit connection for watch.py.

    Never pooled: a LISTEN session must outlive any single operation, and a
    full 8 GB notify queue fails commits on whatever transaction is open.
    The caller closes it.
    """
    import psycopg
    from psycopg.rows import dict_row
    dbjson.install()
    try:
        conn = psycopg.connect(dsn_from_env(), options=_options(),
                               row_factory=dict_row, autocommit=True)
    except Exception as exc:
        raise UnavailableError("listen connection failed: %s" % exc) from exc
    return conn


def reset_after_fork() -> None:
    """Drop the child's inherited pool object WITHOUT closing the parent's
    sockets. Registered with os.register_at_fork(after_in_child=...)."""
    global _pool, _pool_pid
    _pool = None
    _pool_pid = None


def close_pool() -> None:
    global _pool, _pool_pid
    with _lock:
        pool, _pool, _pool_pid = _pool, None, None
    if pool is not None:
        try:
            pool.close()
        except Exception:
            pass


def health() -> dict:
    try:
        pool = get_pool()
        with cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        stats = pool.get_stats()
        size = int(stats.get("pool_size", 0))
        ok = True
    except Exception:
        size, ok = 0, False
    dsn = dsn_from_env()
    host = "unknown"
    for token in dsn.split():
        if token.startswith("host="):
            host = token[5:]
    if host == "unknown" and "://" in dsn:
        tail = dsn.split("://", 1)[1]
        netloc = tail.split("/", 1)[0]
        hostport = netloc.rsplit("@", 1)[-1]
        host = hostport.rsplit(":", 1)[0] or "unknown"
    return {"ok": ok, "size": size, "dsn_host": host}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=reset_after_fork)
