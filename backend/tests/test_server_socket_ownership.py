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


def test_authenticated_duplicate_broker_uuid_may_replace_its_own_worker(monkeypatch):
    import server

    server.clientList = {"test-instance_broker": "old-sid"}
    server.clientOwners = {"test-instance_broker": ("test-instance", "broker")}
    server.brokersList = {"old-sid": {"instance": "test-instance", "symbol": "AAPL"}}
    sio = MagicMock()

    accepted = server.register_socket_client(
        sio, "new-sid",
        {"UUID": "test-instance_broker", "instance": "test-instance", "symbol": "AAPL",
             "control_token": server.derive_socket_control_token(
                 "0123456789abcdef" * 4, "test-instance", "broker")},
        master_key="0123456789abcdef" * 4,
    )

    assert accepted is True
    sio.emit.assert_called_once_with("terminate", {"terminate": True}, room="old-sid")
    assert server.clientList["test-instance_broker"] == "new-sid"


def test_initial_and_cross_instance_claims_require_instance_scoped_proof():
    import server
    sio = MagicMock()
    master = "0123456789abcdef" * 4
    token_broker = server.derive_socket_control_token(master, "instance-a", "broker")
    assert server.register_socket_client(
        sio, "supervisor",
        {"UUID": "instance-a", "instance": "instance-a", "control_token": token_broker},
        master_key=master,
    ) is False
    assert server.register_socket_client(
        sio, "other",
        {"UUID": "instance-b_broker", "instance": "instance-b",
         "control_token": token_broker},
        master_key=master,
    ) is False
    assert server.register_socket_client(
        sio, "bad",
        {"UUID": "wrong", "instance": "instance-a", "control_token": token_broker},
        master_key=master,
    ) is False
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
    other = server.derive_socket_control_token(key, "A_broker", "broker")
    assert not server.register_socket_client(sio, "second", {"UUID": "A_broker", "instance": "A_broker", "control_token": other}, master_key=key)
    server.clientList["x_broker"] = "new"; server.clientOwners["x_broker"] = ("x", "broker")
    server.unregister_socket_client("old")
    assert server.clientList["x_broker"] == "new"


def test_master_key_rejects_predictable_values():
    import server
    assert not server.derive_socket_control_token("x" * 64, "a", "broker")
    assert not server.derive_socket_control_token("not-hex" * 9, "a", "broker")
    assert not server.derive_socket_control_token(
        "0123456789abcdef" * 4, "a", "supervisor")


def test_start_ignores_caller_preflight_and_runs_immutable_image_id(monkeypatch):
    import server
    monkeypatch.setenv("SOCKET_CONTROL_MASTER_KEY", "0123456789abcdef" * 4)
    monkeypatch.setenv("EQUITIES_INSTANCE_AUTOSTART_ALLOWED", "true")
    monkeypatch.setattr(server, "_get_instance_network", lambda c: "net")
    monkeypatch.setattr(server, "_augment_volumes_with_claude", lambda v: v)
    captured = []
    class Images:
        def get(self, image): return type("Image", (), {"id": "sha256:" + "a" * 64})()
    class NotFound(Exception):
        pass
    class Containers:
        def get(self, name): raise NotFound()
        def run(self, *args, **kwargs): captured.append((args, kwargs)); return object()
    client = type("Client", (), {"images": Images(), "containers": Containers()})()
    monkeypatch.setattr(server, "_get_docker_client", lambda: client)
    server.running_threads_objs = {}
    monkeypatch.setattr(
        server, "_fresh_instance_docs",
        lambda _: ({"id": "i", "kind": "equities"}, {}),
    )
    forged = type("Forged", (), {
        "image_id": "sha256:" + "f" * 64,
        "image_digest": "f" * 64,
    })()
    server.start_instance_container("i", preflight=forged)
    args, kwargs = captured[-1]
    assert args[0] == "sha256:" + "a" * 64
    assert kwargs["command"] == ["python", "instance.py", "i"]
    env = kwargs["environment"]
    assert env["INTELLISTOCK_DEPLOYED_ARTIFACT_SHA256"] == "a" * 64
    assert "INSTANCE_SOCKET_SUPERVISOR_TOKEN" not in env
    assert env["INSTANCE_SOCKET_BROKER_TOKEN"]


def test_unknown_preflight_kind_never_touches_container(monkeypatch):
    import server
    monkeypatch.setenv("SOCKET_CONTROL_MASTER_KEY", "0123456789abcdef" * 4)
    class Images:
        def get(self, image): return type("Image", (), {"id": "sha256:" + "a" * 64})()
    class Containers:
        def get(self, name): raise AssertionError("old")
        def run(self, *a, **k): raise AssertionError("run")
    client = type("Client", (), {"images": Images(), "containers": Containers()})()
    monkeypatch.setattr(server, "_get_docker_client", lambda: client)
    monkeypatch.setattr(
        server, "_fresh_instance_docs",
        lambda _: ({"id": "i", "kind": "bad"}, {}),
    )
    assert server.start_instance_container("i") is None


def test_uncertain_old_container_cleanup_blocks_equities_launch(monkeypatch):
    import server

    monkeypatch.setenv("SOCKET_CONTROL_MASTER_KEY", "0123456789abcdef" * 4)
    monkeypatch.setattr(server, "_get_instance_network", lambda c: "net")
    monkeypatch.setattr(server, "_augment_volumes_with_claude", lambda v: v)
    old = type("Old", (), {
        "stop": lambda self, timeout: (_ for _ in ()).throw(
            RuntimeError("stop uncertain")),
        "remove": lambda self: None,
    })()
    class Containers:
        def get(self, name):
            return old

        def run(self, *args, **kwargs):
            raise AssertionError("a duplicate worker must not be launched")
    client = type("Client", (), {
        "images": type("Images", (), {
            "get": lambda self, image: type(
                "Image", (), {"id": "sha256:" + "a" * 64})(),
        })(),
        "containers": Containers(),
    })()
    monkeypatch.setattr(server, "_get_docker_client", lambda: client)
    monkeypatch.setattr(
        server, "_fresh_instance_docs",
        lambda _: ({"id": "i", "kind": "equities"}, {}),
    )
    assert server.start_instance_container("i") is None


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


@pytest.mark.parametrize("failure", ["stop", "remove"])
def test_stop_preserves_authoritative_tracking_on_uncertain_failure(failure):
    import server

    class Container:
        def stop(self, timeout):
            if failure == "stop":
                raise RuntimeError("stop uncertain")

        def remove(self):
            if failure == "remove":
                raise RuntimeError("remove uncertain")

    container = Container()
    server.running_threads = ["i"]
    server.running_threads_objs = {"i": container}
    server.thread_count = 1
    assert server.stop_instance_container("i") is False
    assert server.running_threads_objs == {"i": container}
    assert server.running_threads == ["i"]
    assert server.thread_count == 1


def test_stop_cleans_tracking_only_after_stop_and_remove_succeed():
    import server

    calls = []
    container = type("Container", (), {
        "stop": lambda self, timeout: calls.append(("stop", timeout)),
        "remove": lambda self: calls.append(("remove", None)),
    })()
    server.running_threads = ["i"]
    server.running_threads_objs = {"i": container}
    server.thread_count = 1
    assert server.stop_instance_container("i") is True
    assert calls == [("stop", 5), ("remove", None)]
    assert server.running_threads_objs == {}
    assert server.running_threads == []
    assert server.thread_count == 0


@pytest.mark.parametrize("operation", ["stop", "remove"])
def test_stop_accepts_authoritative_not_found_as_already_gone(operation):
    import server

    class NotFound(Exception):
        pass

    class Container:
        def stop(self, timeout):
            if operation == "stop":
                raise NotFound()

        def remove(self):
            if operation == "remove":
                raise NotFound()

    server.running_threads = ["i"]
    server.running_threads_objs = {"i": Container()}
    server.thread_count = 1
    assert server.stop_instance_container("i") is True
    assert server.running_threads_objs == {}
    assert server.running_threads == []
    assert server.thread_count == 0
