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
        master_key="owner-token",
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
         "control_token": server.derive_socket_control_token("owner-token", "test-instance")},
        master_key="owner-token",
    )

    assert accepted is True
    sio.emit.assert_called_once_with("terminate", {"terminate": True}, room="old-sid")
    assert server.clientList["test-instance"] == "new-sid"


def test_initial_and_cross_instance_claims_require_instance_scoped_proof():
    import server
    sio = MagicMock()
    master = "test-master"
    token_a = server.derive_socket_control_token(master, "instance-a")
    assert server.register_socket_client(sio, "a", {"UUID": "instance-a", "instance": "instance-a"}, master_key=master) is False
    assert server.register_socket_client(sio, "b", {"UUID": "instance-b", "instance": "instance-b", "control_token": token_a}, master_key=master) is False
    assert server.register_socket_client(sio, "bad", {"UUID": "wrong", "instance": "instance-a", "control_token": token_a}, master_key=master) is False
    assert server.register_socket_client(sio, "ok", {"UUID": "instance-a_broker", "instance": "instance-a", "control_token": token_a}, master_key=master) is True


def test_kalshi_funded_mode_is_strict_and_paper_demo_are_not_coerced():
    import pytest
    import server
    live = {"kind": "kalshi", "kalshi_config": {"live_enabled": True, "paper_mode": False}}
    assert server.is_funded_kalshi_live(live, {"kalshi_environment": "live"}) is True
    assert server.is_funded_kalshi_live({"kind": "kalshi", "kalshi_config": {"live_enabled": False, "paper_mode": False}}, {"kalshi_environment": "live"}) is False
    assert server.is_funded_kalshi_live(live, {"kalshi_environment": "demo"}) is False
    with pytest.raises(Exception):
        server.is_funded_kalshi_live({"kind": "kalshi", "kalshi_config": {"live_enabled": "true"}}, {"kalshi_environment": "live"})
