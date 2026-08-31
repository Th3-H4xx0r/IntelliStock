import sys
import inspect
from unittest.mock import MagicMock

import pytest


sys.modules.setdefault("socketio", MagicMock())


def test_live_broker_gate_runs_immediately_before_spawn(monkeypatch):
    import instance as inst
    from live_readiness import LiveReadinessError

    monkeypatch.setattr(inst, "args_list", ["instance.py", "test-instance"])
    monkeypatch.setattr(inst, "broker_process", None)
    monkeypatch.setattr(inst, "_crash_entered", False)
    monkeypatch.setattr(inst, "_crash_loop_latched", False)
    monkeypatch.setattr(inst, "_broker_restart_times", [])
    monkeypatch.setattr(inst, "_maybe_start_alpha_watchdog", lambda *a, **k: None)
    monkeypatch.setattr(
        inst, "_load_instance_and_brokerage",
        lambda _: (
            {"id": "test-instance", "kind": "equities", "brokerage_id": "b"},
            {"id": "b", "brokerage_type": "alpaca", "alpaca_paper": False},
        ),
    )

    def must_not_spawn(*args, **kwargs):
        raise AssertionError("a rejected live report must not reach Popen")

    monkeypatch.setattr(inst.subprocess, "Popen", must_not_spawn)
    with pytest.raises(LiveReadinessError):
        inst.start_broker(["AAPL"])


def test_paper_broker_spawn_does_not_require_live_eligible_report(monkeypatch):
    import instance as inst

    monkeypatch.setattr(inst, "args_list", ["instance.py", "paper-instance"])
    monkeypatch.setattr(inst, "broker_process", None)
    monkeypatch.setattr(inst, "_crash_entered", False)
    monkeypatch.setattr(inst, "_crash_loop_latched", False)
    monkeypatch.setattr(inst, "_broker_restart_times", [])
    monkeypatch.setattr(inst, "_maybe_start_alpha_watchdog", lambda *a, **k: None)
    monkeypatch.setattr(
        inst, "_load_instance_and_brokerage",
        lambda _: (
            {"id": "paper-instance", "kind": "equities", "brokerage_id": "paper"},
            {"id": "paper", "brokerage_type": "alpaca", "alpaca_paper": True},
        ),
    )
    monkeypatch.setattr(
        inst, "_assert_live_broker_start_allowed",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("paper start must not use the funded gate")),
    )
    class Proc:
        def poll(self):
            return None
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *args, **kwargs: Proc())

    inst.start_broker(["AAPL"])
    assert isinstance(inst.broker_process, Proc)


def test_live_broker_gate_allows_matching_deployed_artifact(monkeypatch):
    import instance as inst
    from live_readiness import (ReadinessCheck, ReadinessReport, ReadinessState,
                                report_fingerprint, required_live_checks)
    digest = "d" * 64
    report = ReadinessReport("test-instance", ReadinessState.LIVE_ELIGIBLE,
                             tuple(ReadinessCheck(name, True, "verified", digest)
                                   for name in required_live_checks()), digest)
    payload = {"instance_id": report.instance_id, "state": report.state.value,
               "artifact_hash": digest, "checks": [c.__dict__ for c in report.checks],
               "fingerprint": report_fingerprint(report)}
    monkeypatch.setenv("INTELLISTOCK_DEPLOYED_ARTIFACT_SHA256", digest)
    assert inst._assert_live_broker_start_allowed("test-instance", {"live_readiness_report": payload}) is None


def test_broker_subprocess_receives_only_broker_socket_token(monkeypatch):
    import instance as inst
    monkeypatch.setattr(inst, "args_list", ["instance.py", "test-instance"])
    monkeypatch.setattr(inst, "broker_process", None)
    monkeypatch.setattr(inst, "_crash_entered", False)
    monkeypatch.setattr(inst, "_crash_loop_latched", False)
    monkeypatch.setattr(inst, "_broker_restart_times", [])
    monkeypatch.setattr(inst, "_maybe_start_alpha_watchdog", lambda *args: None)
    monkeypatch.setattr(
        inst, "_load_instance_and_brokerage",
        lambda _: (
            {"id": "test-instance", "kind": "equities", "brokerage_id": "paper"},
            {"id": "paper", "brokerage_type": "alpaca", "alpaca_paper": True},
        ),
    )
    monkeypatch.setenv("INSTANCE_SOCKET_SUPERVISOR_TOKEN", "supervisor")
    monkeypatch.setenv("INSTANCE_SOCKET_BROKER_TOKEN", "broker")
    captured = {}
    class Proc:
        def poll(self): return None
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *a, **kw: captured.update(kw) or Proc())
    inst.start_broker([])
    assert "INSTANCE_SOCKET_SUPERVISOR_TOKEN" not in captured["env"]
    assert captured["env"]["INSTANCE_SOCKET_BROKER_TOKEN"] == "broker"


def test_instance_supervisor_does_not_claim_a_broker_control_identity():
    import instance as inst

    source = inspect.getsource(inst.run)
    assert "INSTANCE_SOCKET_SUPERVISOR_TOKEN" not in source
    assert "socketio.Client()" not in source
