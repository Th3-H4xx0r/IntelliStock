"""Self-healing reconnecting change-watcher runner for the server control plane.

Named for its history; it no longer speaks to the old document database.
``open_feed(conn)`` now returns an iterator of Change dicts -- in practice
``db.watch.feed(TABLE, ...)`` -- and ``get_conn`` returns
``db.pool.listen_connection()``. The signature is unchanged so every call site
ports with an import change only.

Background (2026-07-06 backend outage): the database dropped mid-run and every
control-changefeed thread in ``server.py`` died permanently, because the retry
loops only reconnected on two availability substrings and re-raised everything
else -- including "Connection is closed". A control-plane feed must instead
reconnect whenever the database comes back. This module holds that
reconnect-forever loop, kept dependency-light so it can be unit tested without
importing the server monolith (docker/socketio/waitress).
"""
from __future__ import annotations

import time as _time

# Substrings that indicate a *transient* connection or availability loss
# (reconnect and continue) rather than a genuine programming error. Matched
# case-insensitively against ``str(exc)``.
#
# "primary replica" / "not available" are legacy hints from the old document
# database. They are kept because they are harmless against Postgres text and
# because backend/tests/test_changefeed_selfheal.py -- the regression suite for
# the outage above -- still asserts them; drop them only with that file.
_TRANSIENT_HINTS = (
    "primary replica",
    "not available",
    "connection is closed",
    "connection closed",
    "connection is broken",
    "connection refused",
    "connection reset",
    "server closed the connection",
    "the connection is lost",
    "ssl connection has been closed",
    "terminating connection",
    "broken pipe",
    "bad file descriptor",
    "could not connect",
    "lost contact",
    "timed out",
    "timeout",
)


def is_transient_db_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like a transient connection or availability loss
    that should be retried by reconnecting.

    ``psycopg.OperationalError`` (connection-level failures), the pool's
    ``PoolTimeout``, our own ``UnavailableError``, and OS/socket errors
    (``OSError`` -- e.g. errno 111 "Connection refused",
    ``ConnectionResetError``) are always transient. Anything else falls back to
    substring matching on the message, so an availability failure raised as a
    plain ``Exception`` is still covered without a hard dependency on any
    driver's class hierarchy.
    """
    try:
        import psycopg
        if isinstance(exc, psycopg.OperationalError):
            return True
    except Exception:
        # driver not importable / unexpected layout -- fall through to strings
        pass
    try:
        from psycopg_pool import PoolTimeout
        if isinstance(exc, PoolTimeout):
            return True
    except Exception:
        pass
    try:
        from db.errors import UnavailableError
        if isinstance(exc, UnavailableError):
            return True
    except Exception:
        pass
    if isinstance(exc, (OSError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(hint in msg for hint in _TRANSIENT_HINTS)


# Kept for one release so no call site changes name and body in one commit.
is_transient_rethinkdb_error = is_transient_db_error


def run_reconnecting_changefeed(
    open_feed,
    handle_change,
    label,
    *,
    get_conn,
    log=None,
    pass_conn=True,
    initial_delay=2.0,
    max_delay=30.0,
    sleep=_time.sleep,
    should_continue=None,
):
    """Run a change watcher forever, reconnecting on any transient loss with
    capped exponential backoff.

    Parameters
    ----------
    open_feed : callable(conn) -> iterator of Change dicts
        e.g. ``lambda c: watch.feed("EngineControl", include_initial=True)``.
        Each item is ``{"old_val": doc|None, "new_val": doc|None}``.
    handle_change : callable(change, conn) or callable(change)
        Per-change handler. Called with the connection when ``pass_conn`` is
        True (the default). Handlers are expected to swallow their own errors;
        anything that escapes is treated like a feed error and triggers a
        reconnect (the feed is never allowed to die).
    label : str
        Human name for log lines (e.g. ``"NexusControl"``).
    get_conn : callable() -> connection
        ``db.pool.listen_connection`` in production. Handlers that used the
        connection to issue queries now call ``db.store`` directly and ignore
        it; the parameter stays so no call site's arity changes.
    log : callable(msg, color, service=...) or None
        Optional logger. No-op when None.
    pass_conn : bool
        Pass the live connection to ``handle_change`` (default True).
    initial_delay, max_delay : float
        Backoff bounds in seconds. Backoff resets once the feed DELIVERS a
        change (proof it is genuinely live), not merely on a successful
        connect -- so a connectable-but-unhealthy database still backs off
        toward max_delay instead of reconnecting at the floor.
    sleep, should_continue : callables
        Test seams. ``should_continue()`` gates the loop (default: forever);
        ``sleep(delay)`` defaults to ``time.sleep``.

    Under normal operation this never returns: the feed blocks yielding
    changes, and a dropped connection is logged + retried rather than raised.
    """
    def _log(msg, color):
        if log is None:
            return
        try:
            log(msg, color, service="Postgres")
        except Exception:
            pass

    _cont = should_continue or (lambda: True)
    delay = initial_delay
    while _cont():
        c = None
        try:
            c = get_conn()
            healthy = False
            for change in open_feed(c):
                if not healthy:
                    # A delivered change proves the feed is actually live (not
                    # a connect that then fails) -> only now is it safe to
                    # reset the backoff.
                    healthy = True
                    delay = initial_delay
                if pass_conn:
                    handle_change(change, c)
                else:
                    handle_change(change)
            # An endless feed shouldn't exhaust; if it does the connection is
            # effectively gone -> reconnect.
            _log(f"{label} changefeed ended unexpectedly; reconnecting...", "yellow")
        except Exception as e:  # noqa: BLE001 -- deliberate: keep the feed alive
            if is_transient_db_error(e):
                _log(
                    f"{label} changefeed connection lost ({e}); "
                    f"reconnecting in {delay:.0f}s...",
                    "yellow",
                )
            else:
                _log(
                    f"{label} changefeed error ({e}); reconnecting in {delay:.0f}s...",
                    "red",
                )
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
        sleep(delay)
        delay = min(delay * 1.5, max_delay)
