"""Self-healing RethinkDB changefeed runner for the server control plane.

Background (2026-07-06 backend outage): RethinkDB dropped mid-run and every
control-changefeed thread in ``server.py`` died permanently. The retry loops
only reconnected when the error text contained ``"primary replica"`` or
``"not available"`` and re-raised everything else — including
``"Connection is closed"`` / ``ReqlDriverError`` — so a transient DB blip
killed the (daemon) threads for good, leaving the backend up but deaf to all
control-table changes until a full restart.

A control-plane changefeed must instead reconnect whenever RethinkDB comes
back. This module holds that reconnect-forever loop, kept dependency-light
(only ``time`` + a lazily-imported ``rethinkdb.errors``) so it can be unit
tested without importing the server monolith (docker/socketio/waitress).
"""
from __future__ import annotations

import time as _time

# Substrings that indicate a *transient* RethinkDB connection/availability loss
# (reconnect and continue) rather than a genuine programming error. Matched
# case-insensitively against ``str(exc)``.
_TRANSIENT_HINTS = (
    "primary replica",
    "not available",
    "connection is closed",
    "connection closed",
    "connection refused",
    "connection reset",
    "connection is broken",
    "broken pipe",
    "bad file descriptor",
    "could not connect",
    "lost contact",
    "timed out",
    "timeout",
)


def is_transient_rethinkdb_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like a transient RethinkDB connection or
    availability loss that should be retried by reconnecting.

    ``ReqlDriverError`` (driver/connection-level failures such as
    "Connection is closed.") and OS/socket errors (``OSError`` — e.g. errno
    111 "Connection refused", ``ConnectionResetError``) are always transient.
    Anything else falls back to substring matching on the message so that
    ReqlError availability failures ("primary replica ... not available") are
    also covered without a hard dependency on the driver's class hierarchy.
    """
    try:
        from rethinkdb import errors as _rerr
        if isinstance(exc, _rerr.ReqlDriverError):
            return True
    except Exception:
        # driver not importable / unexpected layout — fall through to strings
        pass
    if isinstance(exc, (OSError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(hint in msg for hint in _TRANSIENT_HINTS)


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
    """Run a RethinkDB changefeed forever, reconnecting on any transient
    connection loss with capped exponential backoff.

    Parameters
    ----------
    open_feed : callable(conn) -> iterable
        Opens the changefeed cursor, e.g.
        ``lambda c: r.db(DB).table(T).changes().run(c)``.
    handle_change : callable(change, conn) or callable(change)
        Per-change handler. Called with the live connection when
        ``pass_conn`` is True (the default). Handlers are expected to swallow
        their own errors; anything that escapes is treated like a feed error
        and triggers a reconnect (the feed is never allowed to die).
    label : str
        Human name for log lines (e.g. ``"NexusControl"``).
    get_conn : callable() -> connection
        Opens a *fresh* RethinkDB connection.
    log : callable(msg, color, service=...) or None
        Optional logger. No-op when None.
    pass_conn : bool
        Pass the live connection to ``handle_change`` (default True).
    initial_delay, max_delay : float
        Backoff bounds in seconds. Backoff resets once the feed delivers a
        change (proof it is genuinely live), not merely on a TCP connect — so a
        connectable-but-unhealthy DB (e.g. primary-replica unavailable) still
        backs off toward max_delay instead of reconnecting at the floor.
    sleep, should_continue : callables
        Test seams. ``should_continue()`` gates the loop (default: forever);
        ``sleep(delay)`` defaults to ``time.sleep``.

    Under normal operation this never returns: ``changes()`` blocks yielding
    changes, and a dropped connection is logged + retried rather than raised.
    """
    def _log(msg, color):
        if log is None:
            return
        try:
            log(msg, color, service="RethinkDB")
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
                    # A delivered change proves the feed is actually live (not a
                    # TCP connect that then fails, e.g. primary-replica
                    # unavailable) -> only now is it safe to reset the backoff.
                    healthy = True
                    delay = initial_delay
                if pass_conn:
                    handle_change(change, c)
                else:
                    handle_change(change)
            # An infinite changefeed shouldn't exhaust; if it does the
            # connection is effectively gone -> reconnect.
            _log(f"{label} changefeed ended unexpectedly; reconnecting...", "yellow")
        except Exception as e:  # noqa: BLE001 — deliberate: keep the feed alive
            if is_transient_rethinkdb_error(e):
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
