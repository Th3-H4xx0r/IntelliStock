import sys
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
    monkeypatch.setattr(inst, "_maybe_start_alpha_watchdog", lambda instance_id: None)
    fake_r = MagicMock()
    (fake_r.db.return_value.table.return_value.get.return_value.run
        .return_value) = {"id": "test-instance", "kind": "equities"}
    monkeypatch.setattr(inst, "r", fake_r)
    monkeypatch.setattr(inst, "get_conn", lambda: MagicMock())

    def must_not_spawn(*args, **kwargs):
        raise AssertionError("a rejected live report must not reach Popen")

    monkeypatch.setattr(inst.subprocess, "Popen", must_not_spawn)
    with pytest.raises(LiveReadinessError):
        inst.start_broker(["AAPL"])


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
    monkeypatch.setattr(inst, "_assert_live_broker_start_allowed", lambda *args: None)
    monkeypatch.setattr(inst, "_maybe_start_alpha_watchdog", lambda *args: None)
    fake_r = MagicMock()
    fake_r.db.return_value.table.return_value.get.return_value.run.return_value = {"id": "test-instance"}
    monkeypatch.setattr(inst, "r", fake_r)
    monkeypatch.setattr(inst, "get_conn", lambda: MagicMock())
    monkeypatch.setenv("INSTANCE_SOCKET_SUPERVISOR_TOKEN", "supervisor")
    monkeypatch.setenv("INSTANCE_SOCKET_BROKER_TOKEN", "broker")
    captured = {}
    class Proc:
        def poll(self): return None
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *a, **kw: captured.update(kw) or Proc())
    inst.start_broker([])
    assert "INSTANCE_SOCKET_SUPERVISOR_TOKEN" not in captured["env"]
    assert captured["env"]["INSTANCE_SOCKET_BROKER_TOKEN"] == "broker"
