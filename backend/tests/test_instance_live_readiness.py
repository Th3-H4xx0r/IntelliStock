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
