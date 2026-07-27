import sys
import types
import pytest
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
        master_key="x" * 32,
    )

    assert accepted is False
    assert server.clientList["test-instance"] == "trusted-sid"
    assert emitted == []


def test_authenticated_duplicate_uuid_may_replace_its_own_worker(monkeypatch):
    import server

    server.clientList = {"test-instance": "old-sid"}
    server.clientOwners = {"test-instance": ("test-instance", "supervisor")}
    server.brokersList = {"old-sid": {"instance": "test-instance", "symbol": "AAPL"}}
    sio = MagicMock()

    accepted = server.register_socket_client(
        sio, "new-sid",
        {"UUID": "test-instance", "instance": "test-instance", "symbol": None,
             "control_token": server.derive_socket_control_token("0123456789abcdef" * 4, "test-instance")},
        master_key="0123456789abcdef" * 4,
    )

    assert accepted is True
    sio.emit.assert_called_once_with("terminate", {"terminate": True}, room="old-sid")
    assert server.clientList["test-instance"] == "new-sid"


def test_initial_and_cross_instance_claims_require_instance_scoped_proof():
    import server
    sio = MagicMock()
    master = "0123456789abcdef" * 4
    token_a = server.derive_socket_control_token(master, "instance-a")
    assert server.register_socket_client(sio, "a", {"UUID": "instance-a", "instance": "instance-a"}, master_key=master) is False
    assert server.register_socket_client(sio, "b", {"UUID": "instance-b", "instance": "instance-b", "control_token": token_a}, master_key=master) is False
    assert server.register_socket_client(sio, "bad", {"UUID": "wrong", "instance": "instance-a", "control_token": token_a}, master_key=master) is False
    token_broker = server.derive_socket_control_token(master, "instance-a", "broker")
    assert server.register_socket_client(sio, "ok", {"UUID": "instance-a_broker", "instance": "instance-a", "control_token": token_broker}, master_key=master) is True


def test_kalshi_funded_mode_is_strict_and_paper_demo_are_not_coerced():
    import pytest
    import server
    live = {"kind": "kalshi", "kalshi_config": {"live_enabled": True, "paper_mode": False}}
    assert server.is_funded_kalshi_live(live, {"kalshi_environment": "live"}) is True
    assert server.is_funded_kalshi_live({"kind": "kalshi", "kalshi_config": {"live_enabled": False, "paper_mode": False}}, {"kalshi_environment": "live"}) is False
    assert server.is_funded_kalshi_live(live, {"kalshi_environment": "demo"}) is False
    with pytest.raises(Exception):
        server.is_funded_kalshi_live({"kind": "kalshi", "kalshi_config": {"live_enabled": "true"}}, {"kalshi_environment": "live"})


def test_image_identity_is_exact_sha256_lowercase():
    import pytest
    import server
    good = type("Image", (), {"id": "sha256:" + "a" * 64})()
    assert server.image_identity(good) == "a" * 64
    for value in ("sha256:" + "A" * 64, "sha256:abc", "abc", "sha512:" + "a" * 64):
        with pytest.raises(Exception):
            server.image_identity(type("Image", (), {"id": value})())


def test_rejected_kalshi_mode_preflight_does_not_mutate_or_restart(monkeypatch):
    import server
    server.running_threads = ["k"]
    server.running_threads_objs = {"k": object()}
    server.thread_count = 1
    monkeypatch.setattr(server, "_preflight_instance_launch", lambda _: (_ for _ in ()).throw(RuntimeError("bad")))
    monkeypatch.setattr(server, "stop_instance_container", lambda _: (_ for _ in ()).throw(AssertionError("stop")))
    monkeypatch.setattr(server, "start_instance_container", lambda *a, **k: (_ for _ in ()).throw(AssertionError("start")))
    server.run_thread_service_change({"old_val": {"id": "k", "kind": "kalshi", "kalshi_config": {"paper_mode": True, "live_enabled": False}}, "new_val": {"id": "k", "runCommand": True, "kind": "kalshi", "kalshi_config": {"paper_mode": False, "live_enabled": True}}}, None)
    assert server.running_threads == ["k"] and server.thread_count == 1


def test_owner_collision_and_old_disconnect_cannot_erase_replacement():
    import server
    sio = MagicMock()
    key = "0123456789abcdef" * 4
    token = server.derive_socket_control_token(key, "A", "broker")
    assert server.register_socket_client(sio, "first", {"UUID": "A_broker", "instance": "A", "control_token": token}, master_key=key)
    other = server.derive_socket_control_token(key, "A_broker", "supervisor")
    assert not server.register_socket_client(sio, "second", {"UUID": "A_broker", "instance": "A_broker", "control_token": other}, master_key=key)
    server.clientList["x"] = "new"; server.clientOwners["x"] = ("x", "supervisor")
    server.unregister_socket_client("old")
    assert server.clientList["x"] == "new"


def test_master_key_rejects_predictable_values():
    import server
    assert not server.derive_socket_control_token("x" * 64, "a")
    assert not server.derive_socket_control_token("not-hex" * 9, "a")


def test_preflight_start_commands_and_child_identity(monkeypatch):
    import server, pytest
    monkeypatch.setenv("SOCKET_CONTROL_MASTER_KEY", "0123456789abcdef" * 4)
    monkeypatch.setattr(server, "_get_instance_network", lambda c: "net")
    monkeypatch.setattr(server, "_augment_volumes_with_claude", lambda v: v)
    captured = []
    class Images:
        def get(self, image): return type("Image", (), {"id": "sha256:" + "a" * 64})()
    class Containers:
        def get(self, name): raise type("NotFound", (Exception,), {})()
        def run(self, *args, **kwargs): captured.append(kwargs); return object()
    client = type("Client", (), {"images": Images(), "containers": Containers()})()
    for kind, command in (("equities", ["python", "instance.py", "i"]), ("kalshi", ["python", "-m", "kalshi.runner", "i"])):
        captured.clear()
        preflight = server.InstanceLaunchPreflight(client, "a" * 64, {"id":"i", "kind":kind}, {})
        server.start_instance_container("i", preflight=preflight)
        assert captured[-1]["command"] == command
        env = captured[-1]["environment"]
        assert env["INTELLISTOCK_DEPLOYED_ARTIFACT_SHA256"] == "a" * 64
        assert env["INSTANCE_SOCKET_SUPERVISOR_TOKEN"] != env["INSTANCE_SOCKET_BROKER_TOKEN"]


def test_unknown_preflight_kind_never_touches_container(monkeypatch):
    import server
    monkeypatch.setenv("SOCKET_CONTROL_MASTER_KEY", "0123456789abcdef" * 4)
    class Images:
        def get(self, image): return type("Image", (), {"id": "sha256:" + "a" * 64})()
    class Containers:
        def get(self, name): raise AssertionError("old")
        def run(self, *a, **k): raise AssertionError("run")
    client = type("Client", (), {"images": Images(), "containers": Containers()})()
    preflight = server.InstanceLaunchPreflight(client, "a" * 64, {"kind":"bad"}, {})
    assert server.start_instance_container("i", preflight=preflight) is None


def test_funded_kalshi_preflight_binds_report_to_fake_image(monkeypatch):
    import server
    from live_readiness import ReadinessCheck, ReadinessReport, ReadinessState, report_fingerprint, required_live_checks
    digest = "a" * 64
    report = ReadinessReport("i", ReadinessState.LIVE_ELIGIBLE, tuple(ReadinessCheck(n, True, "ok", digest) for n in required_live_checks()), digest)
    payload = {"instance_id":"i", "state":report.state.value, "artifact_hash":digest, "checks":[c.__dict__ for c in report.checks], "fingerprint":report_fingerprint(report)}
    monkeypatch.setattr(server, "_fresh_instance_docs", lambda _: ({"id":"i", "kind":"kalshi", "kalshi_config":{"live_enabled":True,"paper_mode":False}, "live_readiness_report":payload}, {"kalshi_environment":"live"}))
    client = type("C", (), {"images": type("I", (), {"get": lambda self, _: type("Image", (), {"id":"sha256:"+digest})()})()})()
    assert server._preflight_instance_launch("i", client=client).image_digest == digest
    payload["artifact_hash"] = "b" * 64
    with pytest.raises(Exception): server._preflight_instance_launch("i", client=client)
