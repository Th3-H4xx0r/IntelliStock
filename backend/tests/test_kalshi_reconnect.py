"""Engine DB reconnect helper: the long-running loop must recover from a closed
connection instead of retrying forever on a dead handle (the 20h stall bug)."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi import db


class FakeConn:
    def __init__(self, *, reconnect_ok=True):
        self.reconnect_ok = reconnect_ok
        self.closed = False
        self.reconnected = False

    def reconnect(self, noreply_wait=False):
        if not self.reconnect_ok:
            raise RuntimeError("Connection is closed.")
        self.reconnected = True
        return self  # driver returns a live connection

    def close(self, noreply_wait=False):
        self.closed = True


def test_reconnect_prefers_driver_reconnect():
    old = FakeConn(reconnect_ok=True)
    new = db.reconnect(old)
    assert new is old and old.reconnected


def test_reconnect_falls_back_to_fresh_conn(monkeypatch):
    fresh = FakeConn()
    monkeypatch.setattr(db, "get_conn", lambda: fresh)
    old = FakeConn(reconnect_ok=False)   # driver reconnect fails
    new = db.reconnect(old)
    assert new is fresh          # opened a brand-new connection
    assert old.closed            # best-effort closed the dead one


def test_reconnect_none_opens_fresh(monkeypatch):
    fresh = FakeConn()
    monkeypatch.setattr(db, "get_conn", lambda: fresh)
    assert db.reconnect(None) is fresh


class ReqlDriverError(Exception):
    pass


def test_is_conn_error_by_type():
    assert db.is_conn_error(ReqlDriverError("boom"))
    assert db.is_conn_error(ConnectionResetError())
    assert db.is_conn_error(BrokenPipeError())


def test_is_conn_error_by_message():
    assert db.is_conn_error(RuntimeError("Connection is closed."))
    assert db.is_conn_error(Exception("lost connection to server"))
    assert db.is_conn_error(OSError("Broken pipe"))


def test_is_conn_error_false_for_logic_errors():
    assert not db.is_conn_error(ValueError("bad value"))
    assert not db.is_conn_error(KeyError("missing"))
    assert not db.is_conn_error(RuntimeError("table does not exist"))
