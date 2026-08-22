"""The self-learning engine must never go quietly inert.

Before the Postgres port the changefeed cursor was bound to the same
connection `_should_run` reads, so a dropped connection raised out of the feed
loop and `run_reconnecting_changefeed` healed it. The feed now lives on
Postgres and cannot see a control-plane blip, so the liveness coupling has to
be restored explicitly -- otherwise one blip leaves completion processing dead
forever while the process still logs as healthy.

These tests need no database: they drive the classification and the reconnect
composition directly.
"""
import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENGINES = os.path.join(_BACKEND, "engines")
for _p in (_BACKEND, _ENGINES):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def sle():
    """Import the daemon WITHOUT leaving the process chdir'd into backend/.

    self_learning_engine.py calls os.chdir(_backend_dir) at import time, which
    would otherwise change the working directory for every test that runs
    after this file.
    """
    cwd = os.getcwd()
    try:
        import self_learning_engine as module
    finally:
        os.chdir(cwd)
    return module


class _FakeReQL:
    """Stands in for the module-level STORE handle.

    EngineControl is an ordinary registry table now, so the control read goes
    through ``db.store.get`` -- but the failure MODES this pins are unchanged:
    a transient connection error must raise, anything else must fail closed.
    """

    def __init__(self, exc=None, doc=None):
        self._exc, self._doc = exc, doc

    def get(self, *a, **k):
        if self._exc is not None:
            raise self._exc
        return self._doc


@pytest.fixture
def logged(sle, monkeypatch):
    lines = []
    monkeypatch.setattr(sle, "_log", lambda m, c="white": lines.append((m, c)))
    return lines


# ---- _should_run: "cannot read" is not the same answer as "turned off" ----

def test_a_transient_control_read_failure_raises_instead_of_going_inert(
        sle, monkeypatch, logged):
    """The regression this whole fix exists for.

    Returning False here is indistinguishable from `running: False`, so a
    single blip used to disable completion processing permanently.
    """
    monkeypatch.setattr(sle, "dbstore",
                        _FakeReQL(ConnectionResetError("connection is closed")))
    with pytest.raises(sle.ControlPlaneUnreadable):
        sle._should_run(object())
    assert any("cannot read EngineControl" in m for m, _ in logged)
    assert any(color == "red" for _, color in logged)


def test_a_non_transient_control_error_still_fails_closed_but_is_logged(
        sle, monkeypatch, logged):
    """Fail-closed is preserved: a malformed control document is still a "no".

    What changed is only that it is no longer SILENT -- and that it does not
    trigger a reconnect storm for what is really a programming error.
    """
    monkeypatch.setattr(sle, "dbstore",
                        _FakeReQL(ValueError("malformed control document")))
    assert sle._should_run(object()) is False
    assert any("cannot read EngineControl" in m for m, _ in logged)


def test_an_unreadable_config_raises_too(sle, monkeypatch, logged):
    """The second read, which group G7 moved onto Postgres.

    `store.get_config` no longer speaks ReQL, so what it raises on an outage is
    a psycopg/pool/UnavailableError -- all of which `is_transient_db_error`
    classifies by isinstance, not by string matching.
    """
    from db.errors import UnavailableError
    monkeypatch.setattr(sle, "dbstore", _FakeReQL(doc={"running": True}))

    def _boom(conn):
        raise UnavailableError("pool exhausted")

    monkeypatch.setattr(sle.store, "get_config", _boom)
    with pytest.raises(sle.ControlPlaneUnreadable):
        sle._should_run(object())
    assert any("cannot read LearningConfig" in m for m, _ in logged)


def test_a_control_document_that_says_off_is_still_just_off(
        sle, monkeypatch, logged):
    """No raise, no log: the operator answering "no" is not a failure."""
    monkeypatch.setattr(sle, "dbstore", _FakeReQL(doc={"running": False}))
    assert sle._should_run(object()) is False
    assert logged == []


def test_control_plane_unreadable_is_catchable_by_the_heartbeat(sle):
    """`_should_run` is shared with the _TURN_INTERVAL_SECONDS heartbeat.

    The heartbeat already self-heals -- its `except Exception` closes beat_conn
    and sets it to None so the next turn reopens it -- but only if the error
    actually reaches it. A BaseException subclass would kill the thread
    outright instead.
    """
    assert issubclass(sle.ControlPlaneUnreadable, RuntimeError)
    assert issubclass(sle.ControlPlaneUnreadable, Exception)


# ---- the composition that proves the coupling is back --------------------

def test_a_dead_control_connection_reaches_the_reconnect_loop(sle, monkeypatch):
    """The property Deviation 1 must not lose.

    `test_changefeed_selfheal.py` covers reconnect-on-FEED-error; nothing
    covered reconnect-on-escaping-HANDLER-error, which is the only route left
    now that the feed is on Postgres and cannot see a control-plane blip.
    """
    from rethink_changefeed import run_reconnecting_changefeed

    conns, calls = [], []

    def _get_conn():
        conns.append(object())
        return conns[-1]

    def _fake_should_run(c):
        calls.append(c)
        if len(calls) == 1:
            raise sle.ControlPlaneUnreadable("EngineControl: connection is closed")
        return False

    monkeypatch.setattr(sle, "_should_run", _fake_should_run)

    def _open_feed(c):
        yield {"old_val": None, "new_val": {"id": "830900", "status": "finished"}}

    run_reconnecting_changefeed(
        _open_feed, sle._make_handler(set()), "T",
        get_conn=_get_conn, log=None, sleep=lambda d: None,
        should_continue=lambda: len(conns) < 2)

    assert len(conns) == 2, "it reconnected instead of sitting inert"
    assert len(calls) == 2, "and processed a change again afterwards"
    assert calls[0] is not calls[1], "on a FRESH connection, not the dead one"


def test_an_ordinary_handler_error_is_still_swallowed(sle, monkeypatch, logged):
    """Only the control-plane failure escapes.

    An unprocessable document must not become a reconnect storm -- that is the
    behaviour the handler's broad `except` was written for, and it stays.
    """
    monkeypatch.setattr(sle, "_should_run", lambda c: True)

    def _boom(conn, run_id, status, processed):
        raise ValueError("unprocessable document")

    monkeypatch.setattr(sle, "_handle_run", _boom)
    handler = sle._make_handler(set())
    handler({"new_val": {"id": "830901", "status": "finished"}}, object())
    assert any("change handler error" in m for m, _ in logged)
