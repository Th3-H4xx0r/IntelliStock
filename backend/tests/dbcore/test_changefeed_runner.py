"""run_reconnecting_changefeed after the port off the old document database.

The signature is unchanged, so these tests drive the loop through the
``sleep`` / ``should_continue`` seams exactly as the legacy regression suite
(backend/tests/test_changefeed_selfheal.py, which must keep passing unchanged)
does. No Postgres needed: nothing here touches a database.
"""
import itertools

import rethink_changefeed as rcf


class _Boom(Exception):
    pass


def test_is_transient_db_error_covers_psycopg_operational_errors():
    import psycopg
    assert rcf.is_transient_db_error(psycopg.OperationalError("server closed"))


def test_is_transient_db_error_covers_pool_timeout():
    from psycopg_pool import PoolTimeout
    assert rcf.is_transient_db_error(PoolTimeout("no connection"))


def test_is_transient_db_error_covers_unavailable_error():
    from db.errors import UnavailableError
    assert rcf.is_transient_db_error(UnavailableError("postgres unavailable"))


def test_is_transient_db_error_covers_os_errors():
    assert rcf.is_transient_db_error(ConnectionResetError(104, "reset"))


def test_is_transient_db_error_matches_substring_hints():
    assert rcf.is_transient_db_error(_Boom("connection is broken"))
    assert rcf.is_transient_db_error(_Boom("could not connect"))
    assert rcf.is_transient_db_error(_Boom("server closed the connection"))
    assert rcf.is_transient_db_error(_Boom("terminating connection due to "
                                           "administrator command"))


def test_is_transient_db_error_rejects_a_programming_error():
    assert rcf.is_transient_db_error(_Boom("column doc does not exist")) is False


def test_the_old_name_is_still_importable():
    assert rcf.is_transient_rethinkdb_error is rcf.is_transient_db_error


def test_no_driver_import_remains():
    """The alias name still spells the old driver; an *import* of it must not.
    (`is_transient_rethinkdb_error` is kept for one release, so a bare
    substring check on the source would be a false positive.)"""
    import inspect
    src = inspect.getsource(rcf)
    assert "import rethinkdb" not in src
    assert "from rethinkdb" not in src


def test_handler_receives_the_connection_when_pass_conn_is_true():
    seen = []
    conns = []

    def get_conn():
        c = object()
        conns.append(c)
        return c

    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([{"old_val": None, "new_val": {"id": 1}}]),
        handle_change=lambda change, conn: seen.append((change, conn)),
        label="t", get_conn=get_conn, sleep=lambda d: None,
        should_continue=lambda: next(counter) < 1)
    assert seen[0][0] == {"old_val": None, "new_val": {"id": 1}}
    assert seen[0][1] is conns[0]


def test_pass_conn_false_calls_the_handler_with_one_argument():
    seen = []
    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([{"old_val": None, "new_val": {"id": 2}}]),
        handle_change=lambda change: seen.append(change),
        label="t", get_conn=lambda: None, pass_conn=False,
        sleep=lambda d: None, should_continue=lambda: next(counter) < 1)
    assert seen == [{"old_val": None, "new_val": {"id": 2}}]


def test_backoff_grows_and_resets_only_after_a_delivered_change():
    delays = []
    state = {"round": 0}

    def open_feed(conn):
        state["round"] += 1
        if state["round"] <= 2:
            raise _Boom("connection is broken")
        return iter([{"old_val": None, "new_val": {"id": 3}}])

    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=open_feed, handle_change=lambda c, x: None, label="t",
        get_conn=lambda: None, sleep=delays.append,
        should_continue=lambda: next(counter) < 3)
    assert delays[0] == 2.0 and delays[1] == 3.0     # 2.0 then 2.0*1.5
    assert delays[2] == 2.0                          # reset after delivery


def test_a_handler_error_reconnects_instead_of_killing_the_feed():
    rounds = {"n": 0}
    logs = []

    def handle(change, conn):
        rounds["n"] += 1
        raise _Boom("handler blew up")

    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([{"old_val": None, "new_val": {"id": 4}}]),
        handle_change=handle, label="t", get_conn=lambda: None,
        log=lambda msg, color, service=None: logs.append((color, msg)),
        sleep=lambda d: None, should_continue=lambda: next(counter) < 3)
    assert rounds["n"] == 3, "kept reconnecting after a handler error"
    assert any(color == "red" for color, _ in logs)


def test_a_closable_connection_is_closed_every_round():
    closed = []

    class Conn:
        def close(self):
            closed.append(True)

    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([]), handle_change=lambda c, x: None,
        label="t", get_conn=Conn, sleep=lambda d: None,
        should_continue=lambda: next(counter) < 2)
    assert len(closed) == 2


def test_a_feed_that_ends_is_logged_and_retried():
    logs = []
    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([]), handle_change=lambda c, x: None,
        label="EngineControl", get_conn=lambda: None,
        log=lambda msg, color, service=None: logs.append((color, msg)),
        sleep=lambda d: None, should_continue=lambda: next(counter) < 1)
    assert any("ended unexpectedly" in msg for _, msg in logs)
