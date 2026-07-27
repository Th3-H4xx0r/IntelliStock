import sys
import types
from unittest.mock import MagicMock


sys.modules.setdefault("socketio", MagicMock())
_waitress = types.ModuleType("waitress")
_waitress.serve = lambda *args, **kwargs: None
sys.modules.setdefault("waitress", _waitress)


def test_unauthenticated_duplicate_uuid_cannot_terminate_existing_worker(monkeypatch):
    import server

    server.clientList = {"test-instance": "trusted-sid"}
    server.brokersList = {"trusted-sid": {"instance": "test-instance", "symbol": "AAPL"}}
    emitted = []
    sio = MagicMock()
    sio.emit.side_effect = lambda *args, **kwargs: emitted.append((args, kwargs))

    accepted = server.register_socket_client(
        sio, "untrusted-sid",
        {"UUID": "test-instance", "instance": "test-instance", "symbol": None},
        control_token="owner-token",
    )

    assert accepted is False
    assert server.clientList["test-instance"] == "trusted-sid"
    assert emitted == []


def test_authenticated_duplicate_uuid_may_replace_its_own_worker(monkeypatch):
    import server

    server.clientList = {"test-instance": "old-sid"}
    server.brokersList = {"old-sid": {"instance": "test-instance", "symbol": "AAPL"}}
    sio = MagicMock()

    accepted = server.register_socket_client(
        sio, "new-sid",
        {"UUID": "test-instance", "instance": "test-instance", "symbol": None,
         "control_token": "owner-token"},
        control_token="owner-token",
    )

    assert accepted is True
    sio.emit.assert_called_once_with("terminate", {"terminate": True}, room="old-sid")
    assert server.clientList["test-instance"] == "new-sid"
