"""The Postgres port removed the module-level RethinkDB handle ``r`` from
interactive_utils, but the live-state actions kept passing it positionally to
the live_state helpers. Every ``/instances/{id}/live-state`` and
``/live-logs`` request then died with ``NameError: name 'r' is not defined``
(observed on strategy-eb, 2026-09-01) — a 500 for EVERY instance, not one.

The helpers ignore that argument, so the actions pass ``None``. These tests
stub the store and broker fetch and only assert the actions RUN: the
NameError fires while the call's arguments are evaluated, so a stubbed callee
still catches a regression.
"""
from __future__ import annotations

import os
import sys

import pytest

_backend = os.path.join(os.path.dirname(__file__), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import interactive_utils as iu  # noqa: E402
import live_state  # noqa: E402
import live_broker_fetch  # noqa: E402


_ROW = {"id": "inst-x", "status": "active", "trading_active": True,
        "last_updated_iso": "2026-09-01T21:00:00+00:00",
        "log_tail": ["boot", "tick #1 COMPLETED"], "uptime_sec": 5}


@pytest.fixture
def stubs(monkeypatch):
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda _conn, _iid: {"id": "inst-x", "runCommand": True})
    monkeypatch.setattr(live_state, "get_live_state", lambda _r, _c, _iid: dict(_ROW))
    monkeypatch.setattr(live_state, "get_command",
                        lambda _r, _c, cid: {"id": cid, "status": "completed"})
    monkeypatch.setattr(live_state, "submit_command", lambda _r, _c, **kw: "cmd-1")
    monkeypatch.setattr(live_state, "complete_command", lambda _r, _c, cid, **kw: None)
    monkeypatch.setattr(live_broker_fetch, "fetch_broker_live_state",
                        lambda _conn, _iid: {"broker_fetch_error": "stubbed: no network",
                                             "broker_fetched_at_iso": "2026-09-01T21:00:00+00:00",
                                             "broker_fetch_age_seconds": 0})


def test_live_state_action_runs_without_a_rethink_handle(stubs):
    out = iu.action_get_live_state(None, "inst-x")
    assert out["id"] == "inst-x"
    assert out["status"] == "active"
    assert out["uptime_sec"] == 5


def test_live_logs_action_falls_back_to_the_db_tail(stubs):
    out = iu.action_live_trading_logs(None, "inst-x", 0)
    assert out["source"] == "db"
    assert out["logs"] == ["boot", "tick #1 COMPLETED"]
    assert out["final_status"] == "running"


def test_live_command_read_and_submit_run(stubs):
    assert iu.action_get_live_command(None, "cmd-1")["status"] == "completed"
    out = iu.action_submit_live_command(None, "inst-x", "halt", {}, "tester")
    assert out == {"command_id": "cmd-1", "status": "pending"}


def test_halting_an_already_stopped_instance_runs(stubs, monkeypatch):
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda _conn, _iid: {"id": "inst-x", "runCommand": False})
    out = iu.action_submit_live_command(None, "inst-x", "halt", {}, "tester")
    assert out["status"] == "completed" and out["result"] == {"already_halted": True}
