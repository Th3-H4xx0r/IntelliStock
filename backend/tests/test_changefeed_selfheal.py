"""Self-healing RethinkDB changefeed runner (server.py control plane).

Regression for the 2026-07-06 backend outage: RethinkDB dropped mid-run and
every control changefeed thread died permanently because the retry loops only
reconnected on "primary replica"/"not available" and re-raised everything else
(incl. "Connection is closed"). A control-plane feed must reconnect on any
transient connection loss instead of killing its daemon thread.

The logic lives in the standalone `rethink_changefeed` module so it can be
tested without importing the server monolith (docker/socketio/waitress).
"""
import sys
import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from rethinkdb import errors as rerr  # noqa: E402

from rethink_changefeed import (  # noqa: E402
    is_transient_rethinkdb_error,
    run_reconnecting_changefeed,
)


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self, *a, **k):
        self.closed = True


# --------------------------------------------------------------------------
# error classification
# --------------------------------------------------------------------------

def test_transient_true_for_connection_loss():
    # the exact class/message from the outage traceback
    assert is_transient_rethinkdb_error(rerr.ReqlDriverError("Connection is closed."))
    assert is_transient_rethinkdb_error(OSError("[Errno 111] Connection refused"))
    assert is_transient_rethinkdb_error(ConnectionResetError("reset by peer"))


def test_transient_true_for_availability_and_connect_messages():
    assert is_transient_rethinkdb_error(Exception("primary replica for shard not available"))
    assert is_transient_rethinkdb_error(Exception("Could not connect to rethinkdb:28015"))


def test_transient_false_for_programming_errors():
    assert not is_transient_rethinkdb_error(ValueError("bad field name"))
    assert not is_transient_rethinkdb_error(KeyError("id"))


# --------------------------------------------------------------------------
# reconnect loop
# --------------------------------------------------------------------------

def _bounded(n):
    """A should_continue() that returns True n times then False."""
    state = {"i": 0}

    def _cont():
        state["i"] += 1
        return state["i"] <= n

    return _cont


def test_reconnects_after_dropped_connection_and_processes_next_change():
    conns = []

    def get_conn():
        c = FakeConn()
        conns.append(c)
        return c

    attempts = {"n": 0}

    def open_feed(c):
        attempts["n"] += 1
        if attempts["n"] == 1:
            # connection dies the moment we open the feed
            raise rerr.ReqlDriverError("Connection is closed.")
        return iter([{"new_val": {"id": "x"}}])

    seen = []
    sleeps = []

    run_reconnecting_changefeed(
        open_feed,
        lambda ch, c: seen.append(ch),
        "Test",
        get_conn=get_conn,
        log=None,
        sleep=lambda d: sleeps.append(d),
        should_continue=_bounded(2),
    )

    assert attempts["n"] == 2, "should reconnect and retry after the drop"
    assert seen == [{"new_val": {"id": "x"}}], "processed the change after reconnect"
    assert all(c.closed for c in conns), "every connection closed on its way out"
    assert sleeps and sleeps[0] == 2.0, "backed off before reconnecting"


def test_drop_mid_stream_does_not_escape():
    """A drop *while iterating* changes must be caught, not propagate out of the
    thread body."""
    def get_conn():
        return FakeConn()

    def open_feed(c):
        def gen():
            yield {"new_val": {"id": "a"}}
            raise rerr.ReqlDriverError("Connection is closed.")
        return gen()

    seen = []
    # one cycle is enough; must return normally (no exception raised)
    run_reconnecting_changefeed(
        open_feed,
        lambda ch, c: seen.append(ch),
        "Test",
        get_conn=get_conn,
        log=None,
        sleep=lambda d: None,
        should_continue=_bounded(1),
    )
    assert seen == [{"new_val": {"id": "a"}}]


def test_non_transient_error_also_reconnects_not_dies():
    """Even an unexpected (non-connection) error must keep the feed alive."""
    attempts = {"n": 0}

    def open_feed(c):
        attempts["n"] += 1
        raise ValueError("unexpected")

    logs = []
    run_reconnecting_changefeed(
        open_feed,
        lambda ch, c: None,
        "Test",
        get_conn=lambda: FakeConn(),
        log=lambda msg, color, service=None: logs.append((color, msg)),
        sleep=lambda d: None,
        should_continue=_bounded(3),
    )
    assert attempts["n"] == 3, "kept retrying despite a non-transient error"
    assert any(color == "red" for color, _ in logs), "non-transient errors logged red"


def test_backoff_grows_and_is_capped_when_feed_never_delivers():
    """Connect succeeds but the feed keeps failing (e.g. primary-replica
    unavailable): backoff must actually GROW toward max_delay, not stay pinned
    at the floor. This is the connectable-but-unhealthy case."""
    sleeps = []

    def open_feed(c):
        raise rerr.ReqlDriverError("Connection is closed.")

    run_reconnecting_changefeed(
        open_feed,
        lambda ch, c: None,
        "Test",
        get_conn=lambda: FakeConn(),  # TCP connect always OK
        log=None,
        sleep=lambda d: sleeps.append(d),
        should_continue=_bounded(10),
        initial_delay=10.0,
        max_delay=15.0,
    )
    assert sleeps[0] == 10.0
    assert 15.0 in sleeps, "backoff must grow past the floor when the feed never recovers"
    assert max(sleeps) <= 15.0, "backoff must not exceed max_delay"


def test_backoff_resets_only_after_a_delivered_change():
    """Backoff must reset when the feed proves healthy (delivers a change), and
    NOT merely because get_conn() succeeded."""
    calls = {"n": 0}

    def open_feed(c):
        calls["n"] += 1
        if calls["n"] == 3:
            return iter([{"id": "ok"}])  # healthy cycle: delivers a change
        raise rerr.ReqlDriverError("Connection is closed.")

    sleeps = []
    run_reconnecting_changefeed(
        open_feed,
        lambda ch, c: None,
        "Test",
        get_conn=lambda: FakeConn(),
        log=None,
        sleep=lambda d: sleeps.append(d),
        should_continue=_bounded(4),
        initial_delay=2.0,
        max_delay=30.0,
    )
    # fail(2)->3, fail(3)->4.5, healthy resets->2, fail(2)->3
    assert sleeps == [2.0, 3.0, 2.0, 3.0]


def test_pass_conn_false_calls_handler_without_connection():
    seen = []
    run_reconnecting_changefeed(
        lambda c: iter([{"id": 1}]),
        lambda ch: seen.append(ch),
        "Test",
        get_conn=lambda: FakeConn(),
        log=None,
        sleep=lambda d: None,
        should_continue=_bounded(1),
        pass_conn=False,
    )
    assert seen == [{"id": 1}]
